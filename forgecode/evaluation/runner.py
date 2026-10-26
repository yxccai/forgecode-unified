"""机制自检与真实模型评测分开；所有配置运行同一个 Runtime。"""
# 【模块框架】实验管理员：同题运行相同核心，切换模块并独立评分。
# 连接位置：eval → self_check 或 run_one → evaluate_files → summarize
# 流过这里的数据：VARIANTS 定义 full/no_planning/no_context/no_memory/no_verification；
# manifest 保存配置与源码指纹。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
import time
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from forgecode.agent.state import initial
from forgecode.app import create_runtime
from forgecode.config import Config
from forgecode.execution.workspace import Workspace, git
from forgecode.fixtures import init_repository
from forgecode.memory import Memory
from forgecode.model import Model
from forgecode.trace import Trace
from forgecode.evaluation.tasks import TASKS
from forgecode.evaluation.oracle import evaluate_files, test_source

VARIANTS = {"full": {}, "no_planning": {"planning": False}, "no_context": {"repo_context": False},
            "no_memory": {"memory": False}, "no_verification": {"verification": False}}

# 【函数框架 create_fixture】为一道评测题搭公开练习场。
# 输入：root 与 Task。 输出：带提交的独立仓库。
# 1. 写 subject.py 与跨文件依赖
# 2. 只公开第一条用例，另加无关文件
# 3. 写 gitignore 并初始化 Git
def create_fixture(root, task):
    # 小例子：pricing_crossfile 的真正错误在 pricing.py，subject.py 只是调用它。
    # 留意：完整独立验收用例不放在 Agent 工作区；但原生命令不是恶意代码隔离环境。
    root = Path(root)
    # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
    root.mkdir(parents=True, exist_ok=True)
    # 字典里的 ** 把 dict(task.extra_files) 的字段展开进来；后面的同名字段覆盖前面的字段。
    for name, source in {"subject.py": task.source, **dict(task.extra_files)}.items():
        # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
        (root / name).write_text(source, encoding="utf-8")
    # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
    # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
    (root / "test_public.py").write_text(test_source(task, task.cases[:1]), encoding="utf-8")
    # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
    (root / ".gitignore").write_text(".forgecode/\n__pycache__/\n", encoding="utf-8")
    for i in range(8):
        # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
        (root / f"unrelated_{i}.py").write_text(f"def unrelated_{i}(x):\n    return x*{i+1}\n", encoding="utf-8")
    init_repository(root)

# 【函数框架 self_check】检查验收器能否拒绝错误、接受参考答案。
# 输入：TASKS 的错误源码、参考源码与测试定义。 输出：oracle_self_check 报告。
# 1. 每题评分错误 baseline
# 2. 再评分参考 solution
# 3. 记录两项是否符合预期
# 4. 所有题都符合才 passed
def self_check():
    """只检查验收器；参考实现来自任务定义，不能作为模型成功率。"""
    # 小例子：sum 错误实现跳首项被拒，正确 sum(values) 被接受。
    # 留意：没有任何模型做题；22 项通过只证明验收器对这些参考检查正常。
    rows = []
    for task in TASKS:
        # 先给验收器一份错误答案：baseline 是题目起始错误实现，下一份是参考答案；检查阅卷器能否分
        # 清对错，不是模型做题成绩。
        # 字典里的 ** 把 dict(task.extra_files) 的字段展开进来；后面的同名字段覆盖前面的字段。
        baseline = evaluate_files(task, {"subject.py": task.source, **dict(task.extra_files)})
        # 字典里的 ** 把 dict(task.solution_files) 的字段展开进来；后面的同名字段覆盖前面的字段。
        reference = evaluate_files(task, {"subject.py": task.solution, **dict(task.solution_files)})
        rows.append({"task": task.id, "category": task.category, "baseline_rejected": not baseline["success"],
                     "reference_accepted": reference["success"], "cases": len(task.cases)})
    # 推导式把循环写紧凑：从 rows 逐项取出 r，收集成迭代器。
    return {
        "mode": "oracle_self_check",
        "tasks": len(rows),
        "passed": all(r["baseline_rejected"] and r["reference_accepted"] for r in rows),
        "rows": rows
    }

# 【函数框架 run_one】让真实模型完成一道题，然后在工作区外验收。
# 输入：task、variant、Config、输出目录、repeat。 输出：成功与否、用量、次数、延迟和失败类
# 别的一行记录。
# 1. 建立题目仓库与 worktree
# 2. 按 variant 装配同一 Runtime
# 3. 运行正式图，只自动批准既定测试 argv
# 4. 捕获实现交给独立验收，检查公开测试未改
# 5. 错误分类、关闭模型，缺用量留空
def run_one(task, variant, config, out, repeat=0):
    # 小例子：full 和 no_context 都做同一道跨文件题，再比较是否找到 pricing.py。
    # 留意：真实调用会访问配置端点；本交付没有跑真实模型成绩，失败基础设施也不能伪装成任务能力
    # 差。
    # 同一核心，只改变模块开关：合并 full 默认与 VARIANTS；identity 绑定 task/variant/repeat。
    # 字典里的 ** 把 VARIANTS[variant] 的字段展开进来；后面的同名字段覆盖前面的字段。
    flags = {
        "planning": True,
        "repo_context": True,
        "memory": True,
        "verification": True,
        **VARIANTS[variant]
    }
    identity = f"{task.id}-{variant}-{repeat}"
    # perf_counter： 读取适合测耗时的单调计时值；用结束减开始，不能拿它当日历日期。
    # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
    events, started = [], time.perf_counter()
    # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
    status, failure, error_type = "error", None, None
    # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
    result, scoring = {}, {"success": False, "passed": 0, "total": len(task.cases)}
    model = None
    # 造独立题目与备用台：公开测试只一条，完整验收另放；记忆引用当前 subject.py。
    # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
    with tempfile.TemporaryDirectory(prefix="forgecode-eval-") as folder:
        # try 中发生匹配的异常会走 except；finally 无论成功、异常或取消都执行，用于收尾。
        try:
            create_fixture(folder, task)
            workspace = Workspace(folder, "eval")
            verify = [sys.executable, "-m", "unittest", "discover"]
            workspace.create(verify)
            Memory(folder).remember("Implementation is subject.py and its imported modules. Preserve test_public.py.", ["subject.py"], confidence=0.8)
            # 装配真实模型与正式 Runtime：这一步访问配置端点才属于真实模型实验，教材交付没有运行此分支。
            trace = Trace(Path(out) / "traces" / (identity + ".jsonl"), events.append, secrets=(config.api_key,), thread_id=identity)
            model = Model(config)
            runtime = create_runtime(workspace.path, model, trace, planning=flags["planning"],
                                     repo_context=flags["repo_context"], memory=flags["memory"], store_root=folder,
                                     context_limit=config.context_char_limit, protected_paths=("test_public.py",))
            prompt = task.instruction + " Inspect source. Do not edit test_public.py. Use the provided verification command."
            # 运行和审批使用相同图：只自动批准已列出的测试 argv；模型预算16、工具40、回修3。
            # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
            with SqliteSaver.from_conn_string(":memory:") as saver:
                graph = runtime.build(saver)
                opts = {"configurable": {"thread_id": identity}, "recursion_limit": 250}
                # invoke： 这里才调用对象提供的执行接口；具体是模型、工具还是图，要看点号左侧的对象。
                result = graph.invoke(initial(prompt, workspace.path, verify, max_steps=16, max_calls=40,
                                              max_repairs=3, verification=flags["verification"]), opts)
                while result.get("__interrupt__"):
                    request = result["__interrupt__"][0].value
                    allowed = request.get("name") == "run_command" and request["args"].get("argv") in (verify, ["python", "-m", "unittest", "discover"], ["python3", "-m", "unittest", "discover"])
                    # invoke： 这里才调用对象提供的执行接口；具体是模型、工具还是图，要看点号左侧的对象。
                    result = graph.invoke(Command(resume=allowed), opts)
            # 捕获代码，独立阅卷：read 最终所有相关文件给 evaluate_files；再查测试文件未被改。终态失败
            # 或独立失败都分类记录。
            status = result["status"]
            # 这里的单星号把列表/序列中的各项展开；原顺序保留。
            names = ["subject.py", *dict(task.extra_files)]
            # 读取实际交付，再独立评分：captured 来自本次工作区。评分调用 evaluate_files 的独立用例，
            # 没有把参考答案当模型提交。
            # 推导式把循环写紧凑：从 names 逐项取出 name，收集成字典。
            captured = {name: (workspace.path / name).read_text(encoding="utf-8") for name in names}
            scoring = evaluate_files(task, captured)
            if git(workspace.path, "diff", "--", "test_public.py"):
                # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
                scoring["success"], failure = False, "verification_tampering"
            if status not in {"answered", "verified"}:
                scoring["success"] = False
                failure = status
            elif not scoring["success"]:
                failure = "independent_acceptance"
        # 基础设施失败也留下类别：关闭客户端由 finally 保证，不能把网络问题混写成模型任务逻辑失败。
        except Exception as exc:
            # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
            failure, error_type = "infrastructure", type(exc).__name__
        finally:
            # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
            if model is not None:
                model.close()
    # 未知用量留空：只有 result 且 usage_missing=0 才报总 tokens；记录延迟、工具和验收结果供受
    # 控汇总。
    # 条件表达式：条件 result and result.get('usage_missing') == 0 成立取前面的值，否则取 else
    #  后面的值。
    # 推导式把循环写紧凑：从 events 逐项取出 e，仅保留满足 e['type'] == 'tool_start' and e['
    # name'] == 'read_file' 的项，收集成集合。
    # 推导式把循环写紧凑：从 events 逐项取出 e，收集成迭代器。
    return {
        "task": task.id,
        "category": task.category,
        "variant": variant,
        "repeat": repeat,
        "success": scoring["success"],
        "tests_passed": scoring["passed"],
        "tests_total": scoring["total"],
        "status": status,
        "failure": failure,
        "exception_type": error_type,
        "steps": result.get("steps", 0),
        "tool_calls": result.get("calls", 0),
        "tokens": result.get("tokens") if result and result.get("usage_missing") == 0 else None,
        "usage_missing": result.get("usage_missing"),
        "latency_seconds": round(time.perf_counter()-started, 3),
        "files_read": len({e["args"].get("path") for e in events if e["type"] == "tool_start" and e["name"] == "read_file"}),
        "verification_runs": sum(e["type"] == "verification" for e in events)
    }

# 【函数框架 summarize】汇总结果，并按同题同 repeat 比较模块差异。
# 输入：实际 rows。 输出：各 variant 的成功率、次数、成本可用性与配对差值。
# 1. 以 task/repeat 索引 full 基准
# 2. 各组算成功率、测试通过率、请求与工具均值
# 3. 只有已知用量进入 token 均值
# 4. 同题配对求 delta，两边都已知才比较 token
def summarize(rows):
    # 小例子：full 成功、no_context 失败，该配对 success_delta=-1。
    # 留意：单次成功率差不能证明因果；题目、模型、重复次数要受控，未知用量不能算零。
    # 给 full 建同题索引：键是 (task,repeat)，避免拿不同题或不同重复轮次乱比。
    report = {}
    # 建立 full 配置对照表：按题目和重复轮次找到 full 记录；这里 baseline 指对照配置，不是修改
    # 前测试。
    # 推导式把循环写紧凑：从 rows 逐项取出 r，仅保留满足 r['variant'] == 'full' 的项，收集成字
    # 典。
    baseline = {(r["task"], r["repeat"]): r for r in rows if r["variant"] == "full"}
    # 分别汇总每个模块配置：success_rate 是任务成功数/运行数；test_pass_rate 是用例通过数/总用
    # 例数，二者分母不同。
    for variant in VARIANTS:
        # 推导式把循环写紧凑：从 rows 逐项取出 r，仅保留满足 r['variant'] == variant 的项，收集成
        # 列表。
        sample = [r for r in rows if r["variant"] == variant]
        if not sample:
            continue
        n = len(sample)
        # 推导式把循环写紧凑：从 sample 逐项取出 r，收集成迭代器。
        # 推导式把循环写紧凑：从 {r['failure'] for r in sample if r['failure']} 逐项取出 kind，收
        # 集成字典。
        # 推导式把循环写紧凑：从 sample 逐项取出 r，仅保留满足 r['failure'] 的项，收集成集合。
        item = {
            "tasks": n,
            "success_rate": sum(r["success"] for r in sample)/n,
            "test_pass_rate": sum(r["tests_passed"] for r in sample)/sum(r["tests_total"] for r in sample),
            "mean_steps": sum(r["steps"] for r in sample)/n,
            "mean_tool_calls": sum(r["tool_calls"] for r in sample)/n,
            "mean_latency_seconds": sum(r["latency_seconds"] for r in sample)/n,
            "failures": {kind: sum(r["failure"] == kind for r in sample) for kind in {r["failure"] for r in sample if r["failure"]}}
        }
        # 把未知用量单独留下：None 不进已知数值列表。后面同时报告已知均值和未知条数，不能拿 0 冒充
        # 未知。
        # 推导式把循环写紧凑：从 sample 逐项取出 r，仅保留满足 r['tokens'] is not None 的项，收集
        # 成列表。
        # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
        known = [r["tokens"] for r in sample if r["tokens"] is not None]
        # 条件表达式：条件 known 成立取前面的值，否则取 else 后面的值。
        item.update(mean_tokens=sum(known)/len(known) if known else None, usage_known_runs=len(known), usage_unknown_runs=n-len(known))
        # 只比较同题同轮：当前配置的一条记录，要找到 full 中同一题同一轮才能配对。缺一边就不计算这
        # 一对。
        # 推导式把循环写紧凑：从 sample 逐项取出 r，仅保留满足 (r['task'], r['repeat']) in
        # baseline 的项，收集成列表。
        pairs = [(r, baseline[(r["task"], r["repeat"])]) for r in sample if (r["task"], r["repeat"]) in baseline]
        if variant != "full" and pairs:
            # 推导式把循环写紧凑：从 pairs 逐项取出 (r, b)，仅保留满足 r['tokens'] is not None and b['
            # tokens'] is not None 的项，收集成列表。
            # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
            token_pairs = [(r, b) for r, b in pairs if r["tokens"] is not None and b["tokens"] is not None]
            # 推导式把循环写紧凑：从 pairs 逐项取出 (r, b)，收集成迭代器。
            # 条件表达式：条件 token_pairs 成立取前面的值，否则取 else 后面的值。
            # 推导式把循环写紧凑：从 token_pairs 逐项取出 (r, b)，收集成迭代器。
            item["paired"] = {
                "n": len(pairs),
                "success_delta": sum(int(r["success"])-int(b["success"]) for r, b in pairs)/len(pairs),
                "latency_delta": sum(r["latency_seconds"]-b["latency_seconds"] for r, b in pairs)/len(pairs),
                "token_pair_count": len(token_pairs),
                "token_delta": sum(r["tokens"]-b["tokens"] for r, b in token_pairs)/len(token_pairs) if token_pairs else None
            }
        report[variant] = item
    return report

# 【函数框架 main】区分离线验收器自检与真实模型实验，保存可复核配置。
# 输入：--offline、题目 ID、variants、repeats、config、out。 输出：自检 JSON，或 manifest/
# results.jsonl/summary.json。
# 1. 离线分支只做 self_check
# 2. 真实分支校验题目和重复次数并读取配置
# 3. 计算源码与数据集指纹，拒绝复用不一致目录
# 4. 加载已完成任务、固定种子打乱剩余任务
# 5. 逐项 run_one，追加结果并更新汇总
def main(argv=None):
    # 小例子：--offline 不访问端点；真实实验命令必须另配模型。
    # 留意：种子只固定本地任务顺序，不保证远程模型每次完全相同。
    parser = argparse.ArgumentParser(description="独立验收自检 / 真实模型同题评测")
    parser.add_argument("--offline", action="store_true", help="只检查验收器，不调用模型")
    parser.add_argument("--out", default="artifacts/evaluation")
    parser.add_argument("--config")
    parser.add_argument("--model")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--tasks", nargs="+", help="指定任务 ID")
    parser.add_argument("--variants", nargs="+", choices=tuple(VARIANTS), default=["full"])
    args = parser.parse_args(argv)
    # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
    out = Path(args.out).resolve()
    # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
    out.mkdir(parents=True, exist_ok=True)
    if args.offline:
        report = self_check()
        # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        (out / "oracle-self-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        print(json.dumps({"mode": report["mode"], "tasks": report["tasks"], "passed": report["passed"]}, ensure_ascii=False))
        if not report["passed"]:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise SystemExit(1)
        return
    if args.repeats < 1:
        parser.error("repeats must be positive")
    # 推导式把循环写紧凑：从 TASKS 逐项取出 t，仅保留满足 not args.tasks or t.id in args.tasks
    #  的项，收集成列表。
    selected = [t for t in TASKS if not args.tasks or t.id in args.tasks]
    # 推导式把循环写紧凑：从 TASKS 逐项取出 t，收集成集合。
    if not selected or (args.tasks and set(args.tasks)-{t.id for t in TASKS}):
        parser.error("Unknown task ID")
    cfg = Config.load(args.config, model=args.model)
    # 推导式把循环写紧凑：从 selected 逐项取出 t，收集成列表。
    # hexdigest： 把摘要转成十六进制文字，便于作为字典值或保存编号。
    # sha256： 把内容算成固定摘要；用来比对版本或参数，不是加密后可以再还原的文字。
    # 推导式把循环写紧凑：从 sorted(Path(__file__).parents[1].rglob('*.py')) 逐项取出 p，收集
    # 成迭代器。
    # as_posix： 把路径表示成使用 / 的文字，便于跨模块保存和比对。
    # relative_to： 取相对于指定根目录的路径；不在该根下会报错，不能用字符串前缀冒充这个检查。
    # read_bytes： 读取文件的原始字节；摘要计算以这些实际内容为依据。
    # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
    # 推导式把循环写紧凑：从 TASKS 逐项取出 t，收集成列表。
    # asdict： 把 dataclass 的字段转为普通字典，随后可编码为 JSON。
    metadata = {
        "mode": "real_model",
        "model": cfg.model,
        "reasoning_effort": cfg.reasoning_effort,
        "base_url": cfg.base_url,
        "repeats": args.repeats,
        "tasks": [t.id for t in selected],
        "variants": args.variants,
        "seed": 42,
        "runtime": {"timeout": cfg.timeout, "streaming": cfg.streaming,
                            "supports_tools": cfg.supports_tools, "supports_usage": cfg.supports_usage,
                            "context_char_limit": cfg.context_char_limit, "max_steps": 16,
                            "max_calls": 40, "max_repairs": 3},
        "source_sha256": hashlib.sha256(b"".join(
                    p.relative_to(Path(__file__).parents[1]).as_posix().encode()+b"\0"+p.read_bytes()
                    for p in sorted(Path(__file__).parents[1].rglob("*.py")))).hexdigest(),
        "dataset_sha256": hashlib.sha256(json.dumps([asdict(t) for t in TASKS], sort_keys=True).encode()).hexdigest()
    }
    manifest = out / "manifest.json"
    # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
    # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
    if manifest.exists() and json.loads(manifest.read_text(encoding="utf-8")) != metadata:
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise ValueError("Output directory belongs to another evaluation")
    # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
    # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
    manifest.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    results = out / "results.jsonl"
    # 条件表达式：条件 results.exists() 成立取前面的值，否则取 else 后面的值。
    # 推导式把循环写紧凑：从 results.read_text(encoding='utf-8').splitlines() 逐项取出 line，
    # 收集成列表。
    # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
    # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()] if results.exists() else []
    # 推导式把循环写紧凑：从 rows 逐项取出 r，收集成集合。
    done = {(r["task"], r["variant"], r["repeat"]) for r in rows}
    # 推导式把循环写紧凑：从 range(args.repeats) 逐项取出 i；再从 selected 逐项取出 t；再从
    # args.variants 逐项取出 v，仅保留满足 (t.id, v, i) not in done 的项，收集成列表。
    jobs = [(t, v, i) for i in range(args.repeats) for t in selected for v in args.variants if (t.id, v, i) not in done]
    random.Random(42).shuffle(jobs)
    for task, variant, repeat in jobs:
        row = run_one(task, variant, cfg, out, repeat)
        rows.append(row)
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with results.open("a", encoding="utf-8") as stream:
            # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        print(json.dumps(row, ensure_ascii=False), flush=True)
        # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        (out / "summary.json").write_text(json.dumps(summarize(rows), ensure_ascii=False, indent=2), encoding="utf-8")
