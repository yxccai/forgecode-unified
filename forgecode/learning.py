"""离线课程调用正式 Runtime；只有模型回复是预设的。"""
# 【模块框架】可重复参观路线：预设模型回复，真实执行工具和测试。
# 连接位置：cli learn → demo → fixture/Workspace → create_runtime(ScriptModel) → 正式图
# 流过这里的数据：TOPICS/FOCUS 只控制终端显示；完整运行相同。summary/events 供学习台构建。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from forgecode.agent.state import initial
from forgecode.app import create_runtime
from forgecode.execution.workspace import Workspace
from forgecode.fixtures import fixture
from forgecode.memory import Memory
from forgecode.model import ScriptModel
from forgecode.trace import Trace

TOPICS = {"loop": "模型与循环", "tools": "工具合同", "context": "上下文与检索",
          "state": "状态与恢复", "memory": "长期记忆", "planning": "规划与反馈",
          "execution": "执行控制", "evaluation": "轨迹与评测"}

FOCUS = {
    "loop": {"node", "decision"},
    "tools": {"tool_end", "approval"},
    "context": {"request", "retrieval"},
    "state": {"state", "approval"},
    "memory": {"memory", "memory_filter"},
    "planning": {"plan", "verification", "feedback"},
    "execution": {"approval", "tool_end", "verification"},
    "evaluation": {"verification", "finish"},
}

# 【函数框架 action】简洁构造一张工具调用单。
# 输入：name、args、call_id。 输出：含 name/args/id 的字典。
# 1. 保存动作名
# 2. 保存参数
# 3. 保存调用 ID
def action(name, args, call_id):
    # 小例子：action('read_file',{'path':'app.py'},'read')。
    # 留意：构造数据不是执行工具。
    return {"name": name, "args": args, "id": call_id}

# 【函数框架 demo】跑完从错误实现到反馈修复的一次真实机制演示。
# 输入：独立输出 out、可选显示主题。 输出：summary 与实际 events，并保存样例、日志、存档与
#  patch。
# 1. 创建全新样例 Git 仓库与 worktree，预存来源笔记
# 2. 设置六条模型回复，包括 a*b 错改
# 3. 装配正式 Runtime 与 SQLite checkpoint
# 4. 仅批准这段演示明确的编辑/测试
# 5. 完成后导 patch 和 summary，核对原样例仍为 a-b
def demo(out, topic="all"):
    # 小例子：真实终点 verified，经历一次测试失败回修、六次模型请求。
    # 留意：只有模型回复预设；不能把演示通过率当真实模型成功率。
    # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
    out = Path(out).resolve()
    # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
    out.mkdir(parents=True, exist_ok=True)
    # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
    run = out / ("run-" + uuid4().hex[:10])
    # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
    run.mkdir()
    repository = fixture(run / "repository")
    verify = [sys.executable, "-m", "unittest", "discover"]
    workspace = Workspace(repository, "lesson")
    workspace.create(verify)
    Memory(repository).remember("The add function is in app.py. Verify add with test_app.py.", ["app.py"], confidence=0.8)
    model = ScriptModel([
        "先读取 add 的实现，再精确修改，最后依据真实测试反馈修复。",
        AIMessage(content="", tool_calls=[action("list_files", {}, "files"), action("read_file", {"path": "app.py"}, "read")]),
        AIMessage(content="", tool_calls=[action("edit_file", {"path": "app.py", "old": "a - b", "new": "a * b"}, "wrong-edit")]),
        "第一次修改已完成，请运行最终验证。",
        AIMessage(content="", tool_calls=[action("read_file", {"path": "app.py"}, "re-read"),
                                         action("edit_file", {"path": "app.py", "old": "a * b", "new": "a + b"}, "repair"),
                                         action("run_command", {"argv": verify}, "test")]),
        "已根据失败输出修复 add；以程序的最终验证结果为准。"
    ])
    events = []
    trace_path = run / "trace.jsonl"
    trace = Trace(trace_path, events.append, thread_id="lesson")
    runtime = create_runtime(workspace.path, model, trace, store_root=repository, mode="interactive",
                             protected_paths=("test_app.py",))
    # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = runtime.build(saver)
        options = {"configurable": {"thread_id": "lesson"}, "recursion_limit": 200}
        # invoke： 这里才调用对象提供的执行接口；具体是模型、工具还是图，要看点号左侧的对象。
        result = graph.invoke(initial("Fix add(a,b) in app.py; inspect source, preserve tests and verify.", workspace.path, verify), options)
        while result.get("__interrupt__"):
            request = result["__interrupt__"][0].value
            allowed = (request["name"] == "edit_file" and request["args"]["path"] == "app.py") or (request["name"] == "run_command" and request["args"]["argv"] == verify)
            # invoke： 这里才调用对象提供的执行接口；具体是模型、工具还是图，要看点号左侧的对象。
            result = graph.invoke(Command(resume=allowed), options)
    patch = workspace.export()
    # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
    summary = {
        "mode": "scripted_learning",
        "topic": topic,
        "status": result["status"],
        "model_calls": result["steps"],
        "tool_attempts": result["calls"],
        "repairs": result["repairs"],
        "tokens": None,
        "baseline_passed": result["baseline"]["passed"],
        "final_passed": result["verification"]["passed"],
        "trace": str(trace_path),
        "checkpoint": str(run / "checkpoints.sqlite"),
        "patch": str(patch),
        "workspace": str(workspace.path),
        "source_repository_preserved": "a - b" in (repository / "app.py").read_text(encoding="utf-8")
    }
    # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
    # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary, events

# 【函数框架 main】提供离线学习命令并打印选定观察视角。
# 输入：--topic/--out/--json。 输出：终端输出与 demo 文件。
# 1. 解析主题并调用完整 demo
# 2. JSON 模式输出汇总
# 3. 普通模式筛选展示本章相关事件
# 4. 终点非 verified 用失败退出
def main(argv=None):
    # 小例子：start.ps1 learn --topic state 看游标与审批，同一次完整机制。
    # 留意：--topic context 没有换一个简化 Agent，只是过滤终端镜头。
    parser = argparse.ArgumentParser(description="离线学习：同一 Runtime，真实文件/审批/测试/恢复，预设模型回复")
    # 这里的单星号把列表/序列中的各项展开；原顺序保留。
    parser.add_argument("--topic", choices=("all", *TOPICS), default="all")
    parser.add_argument("--out", default="artifacts/learning")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
    summary, events = demo(args.out, args.topic)
    if args.json:
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("ForgeCode 离线学习｜" + TOPICS.get(args.topic, "完整调用链"))
        print("脚本模型用于学习机制；以下工具和验证结果来自真实执行。")
        if args.topic != "all":
            print("完整运行保持相同，终端只聚焦本章事件；完整轨迹仍保存。")
        for event in events:
            if args.topic != "all" and event["type"] not in FOCUS[args.topic]:
                continue
            if event["type"] == "node" and event["phase"] == "start":
                print(" → " + event["node"])
            elif event["type"] == "verification":
                print(f"   验证 {event['phase']}: exit={event['exit_code']}, passed={event['passed']}")
            elif event["type"] == "tool_end":
                print(f"   工具 {event['name']}: {event['result_status']}")
            elif event["type"] in {"request", "retrieval", "state", "memory", "memory_filter", "plan", "feedback", "approval"}:
                # 推导式把循环写紧凑：从 event.items() 逐项取出 (k, v)，仅保留满足 k not in {'messages', '
                # time', 'run_id', 'thread_id'} 的项，收集成字典。
                details = {k: v for k, v in event.items() if k not in {"messages", "time", "run_id", "thread_id"}}
                # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
                print("   " + json.dumps(details, ensure_ascii=False))
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("阅读 docs/showcase-demo.md，查看复现步骤、产物与源码入口。")
    if summary["status"] != "verified":
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise SystemExit(1)
