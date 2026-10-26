"""单次任务：明确工作区与验证命令，保留失败现场，导出 patch。"""
# 【模块框架】单次仓库任务接待门：建备用台、执行、审批、保存交付包。
# 连接位置：cli run → batch.main → Workspace → create_runtime → graph.invoke → export
# 流过这里的数据：命令行 task/repo/verify/预算；thread 标识任务，JSONL 与 SQLite 保存过程。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import argparse
import json
from pathlib import Path
import shlex
from uuid import uuid4
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from forgecode.agent.state import initial
from forgecode.app import create_runtime
from forgecode.config import Config
from forgecode.execution.workspace import Workspace
from forgecode.model import Model
from forgecode.trace import Trace
from forgecode.ui import Terminal

# 【函数框架 main】完成一次明确仓库任务，或从存档继续。
# 输入：argv 中的 task、--repo、--verify、--resume、开关和预算。 输出：worktree、日志、
# checkpoint、patch；非 verified 用退出码 1。
# 1. 解析并校验参数，读取配置
# 2. 新建/核对 Workspace 并装配 Runtime
# 3. 打开 SqliteSaver，建立新状态或恢复
# 4. 遇 interrupt 显示本次动作并读取批准
# 5. 结束导 patch；finally 关闭界面与网络客户端
def main(argv=None):
    # 小例子：run '修复 add' --repo 样例根 --verify 'python -m unittest discover'。
    # 留意：新任务要求明确验证命令；恢复要求兼容状态与原工作台，失败现场不自动销毁。
    # 解析一次明确接单：新任务必须 task/repo/verify，恢复必须 thread；各预算先验合法。
    parser = argparse.ArgumentParser(description="ForgeCode 仓库任务（同一 Runtime）")
    parser.add_argument("task", nargs="?")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--config")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--thread")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify", help="用户预授权的验证命令；新任务必填")
    parser.add_argument("--no-planning", action="store_true")
    parser.add_argument("--no-context", action="store_true")
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-calls", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=0)
    parser.add_argument("--max-repairs", type=int, default=3)
    args = parser.parse_args(argv)
    if args.resume and not args.thread:
        parser.error("--resume requires --thread")
    if not args.resume and (not args.task or not args.verify):
        parser.error("New tasks require task and --verify")
    if args.max_steps < 1 or args.max_calls < 1 or args.max_tokens < 0 or args.max_repairs < 0:
        parser.error("Invalid budgets")
    # 建备用台并接录像：新任务 create，旧任务 load；Model 与策略通过 app 统一装配。
    cfg = Config.load(args.config, model=args.model, base_url=args.base_url, max_steps=args.max_steps)
    # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
    thread = args.thread or uuid4().hex[:12]
    workspace = Workspace(args.repo, thread)
    # 条件表达式：条件 args.resume 成立取前面的值，否则取 else 后面的值。
    metadata = workspace.load() if args.resume else workspace.create(shlex.split(args.verify))
    ui = Terminal()
    trace = Trace(workspace.data / "traces" / (thread + ".jsonl"), ui, secrets=(cfg.api_key,), thread_id=thread)
    runtime = create_runtime(workspace.path, Model(cfg), trace, planning=not args.no_planning,
                             repo_context=not args.no_context, memory=not args.no_memory,
                             store_root=workspace.root, context_limit=cfg.context_char_limit)
    options = {"configurable": {"thread_id": thread}, "recursion_limit": 1000}
    # try 中发生匹配的异常会走 except；finally 无论成功、异常或取消都执行，用于收尾。
    try:
        # 状态创建或继续：恢复检查 snapshot.next、schema_version 和根；新任务 initial 清楚填入各上
        # 限。
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with SqliteSaver.from_conn_string(str(workspace.data / "tasks.sqlite")) as saver:
            graph = runtime.build(saver)
            if args.resume:
                # get_state： 读取图保存的进度与下一节点；不是重新运行旧工具。
                snapshot = graph.get_state(options)
                if not snapshot.next or snapshot.values.get("schema_version") != 1 or snapshot.values.get("root") != str(workspace.path):
                    # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                    raise ValueError("No compatible interrupted task to resume")
                payload = None
            else:
                payload = initial(args.task, workspace.path, metadata["verify"], args.max_steps, args.max_calls, args.max_tokens, args.max_repairs)
            # 处理每次审批暂停：显示本次请求，Command(resume=...) 继续；无 interrupt 才完成。
            while True:
                # get_state： 读取图保存的进度与下一节点；不是重新运行旧工具。
                snapshot = graph.get_state(options)
                # 推导式把循环写紧凑：从 snapshot.tasks 逐项取出 task；再从 task.interrupts 逐项取出 item，
                # 收集成列表。
                interrupts = [item for task in snapshot.tasks for item in task.interrupts]
                if interrupts:
                    ui.close()
                    # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
                    ui.console.print(json.dumps(interrupts[0].value, ensure_ascii=False, indent=2), markup=False)
                    payload = Command(resume=input("批准本次动作? [y/N] ").strip().lower() == "y")
                # invoke： 这里才调用对象提供的执行接口；具体是模型、工具还是图，要看点号左侧的对象。
                result = graph.invoke(payload, options)
                if not result.get("__interrupt__"):
                    break
                payload = None
        # 无论结果如何，留下可查现场：verified 以外用失败退出；异常分支同样导 patch，finally 关闭
        # 界面和模型连接。
        patch = workspace.export()
        ui.console.print(f"Status: {result['status']}\nPatch: {patch}\nThread: {thread}", markup=False)
        if result["status"] != "verified":
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise SystemExit(1)
    except (Exception, KeyboardInterrupt) as exc:
        ui.close()
        ui.console.print(f"任务保留：{workspace.path}\nThread: {thread}\n原因：{type(exc).__name__}", markup=False)
        workspace.export()
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise SystemExit(1)
    finally:
        ui.close()
        runtime.model.close()
