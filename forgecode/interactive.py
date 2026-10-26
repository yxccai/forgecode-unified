"""Terminal REPL, slash commands and configuration; no agent decisions here."""
# 【模块框架】聊天接待台：处理斜杠命令、配置、会话选择，再交给 Session。
# 连接位置：cli chat → interactive.main → Session.turn → Runtime
# 流过这里的数据：profile 是选定配置文件，thread 是当前会话；每轮 Model 用完即关闭。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import shlex
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from forgecode.config import Config
from forgecode.menus import api_menu, pick_effort, pick_model, save
from forgecode.history import list_sessions, select_session, show_history
from forgecode.session import Session
from forgecode.trace import Trace
from forgecode.ui import Terminal
from forgecode.model import Model


HELP = """/model [NAME]    Select or enter a model
/effort [LEVEL]  Select reasoning effort; 'default' omits the field
/api             Open API settings menu (URL, key, model, effort)
/url URL         Switch OpenAI-compatible endpoint
/key             Enter API key without echo
/config          Open API settings menu
/save            Save current settings for next launch
/verify COMMAND  Set trusted test command; 'off' disables automatic verification
/status          Show session and model, never the key
/resume          Continue an interrupted turn
/sessions        Browse and reopen saved conversations in this directory
/history [all]   Read messages; 'all' also includes tools and runtime observations
/new             Start a fresh conversation
/exit            Exit
"""


# 【函数框架 load_config】兼容普通配置与可选 Windows 环境桥接。
# 输入：path、windows 开关。 输出：可在未配置时打开菜单的 Config。
# 1. 必要时尝试 windows_environment
# 2. 显式交互配置作为 overrides，优先于继承环境
# 3. 没有模型时建立 unconfigured 占位，再置空等待用户设置
def load_config(path=None, windows=False):
    # 小例子：第一次 chat 先进入 /api 选模型，再提交修理任务。
    # 留意：占位让菜单能先启动，不代表可以用空模型发任务。
    if windows or (not path and not os.getenv("FORGECODE_MODEL") and not os.getenv("OPENAI_MODEL")
                   and not Path("config.local.json").exists()):
        try:
            from forgecode.windows import windows_environment
            windows_environment()
        except (OSError, RuntimeError, ValueError):
            if windows:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise
        except Exception:
            if windows:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise
    # 条件表达式：条件 Path('config.local.json').exists() 成立取前面的值，否则取 else 后面的值。
    target = path or ("config.local.json" if Path("config.local.json").exists() else None)
    if target:
        # An explicitly selected interactive profile overrides inherited environment.
        # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
        # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
        values = json.loads(Path(target).read_text(encoding="utf-8"))
        # 调用时的 ** 把字典按参数名展开；字段 key 的值相当于用 key=值 传给函数。
        # 字典里的 ** 把 values 的字段展开进来；后面的同名字段覆盖前面的字段。
        result = Config.load(target, **{**values, "model": values.get("model") or "unconfigured"})
        result.model = values.get("model", "")
        return result
    try:
        return Config.load()
    except ValueError:
        result = Config.load(model="unconfigured")
        result.model = ""
        return result


# 【函数框架 main】运行终端接待循环，把交互与 Agent 核心分开。
# 输入：--repo/--config/--session/--sessions 等命令行参数与用户输入。 输出：聊天展示、配置
# 文件、存档及运行日志。
# 1. 校验仓库并准备数据目录
# 2. 加载配置、选择会话、打开 saver
# 3. 循环处理 /api、/history、/new、/verify 等菜单
# 4. 普通任务创建 Model，调用 Session.turn，finally 关闭
# 5. 处理退出、中断与脱敏错误，最终关闭界面
def main():
    # 小例子：/verify python -m unittest discover 设置可信测试，再输入 Fix add。
    # 留意：交互编辑会影响用户指定目录且要审批；离线课程用自己的新样例目录。
    # 接待门参数与目录：校验工作根、数据目录，确定会话ID与当前配置。
    parser = argparse.ArgumentParser(description="ForgeCode interactive terminal")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--config")
    parser.add_argument("--windows-env", action="store_true")
    parser.add_argument("--session", help="Reopen a saved conversation ID")
    parser.add_argument("--sessions", action="store_true", help="Choose a saved conversation at startup")
    args = parser.parse_args()
    # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
    root = Path(args.repo).resolve()
    if not root.is_dir():
        parser.error("Repository directory does not exist")
    data = root / ".forgecode"
    # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
    data.mkdir(exist_ok=True)
    ui = Terminal()
    # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
    thread = args.session or uuid4().hex[:12]
    # try 中发生匹配的异常会走 except；finally 无论成功、异常或取消都执行，用于收尾。
    try:
        # 条件表达式：条件 args.config 成立取前面的值，否则取 else 后面的值。
        profile = Path(args.config) if args.config else data / "config.local.json"
        # 条件表达式：条件 profile.exists() or args.config 成立取前面的值，否则取 else 后面的值。
        config = load_config(str(profile) if profile.exists() or args.config else None, args.windows_env)
        # 数据库在接待期间保持打开：选择/核对旧会话，open_session 组合正式 Runtime。历史展示不会重
        # 跑工具。
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with SqliteSaver.from_conn_string(str(data / "interactive.sqlite")) as saver:
            if args.sessions:
                selected = select_session(ui.console, list_sessions(saver, root), thread)
                if selected:
                    thread = selected
            if args.session and not saver.get_tuple({"configurable": {"thread_id": thread}}):
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Saved session not found; use forge --sessions")
            # 【函数框架 main.open_session】用当前 thread 和配置打开一个会话接待对象。
            # 输入：外层 root/thread/saver/config。 输出：新 Session。
            # 1. 用当前 thread 生成日志路径
            # 2. Trace 用函数读取最新密钥
            # 3. 创建 Session 和上限
            def open_session():
                # 小例子：切换保存会话后 open_session 读取同一 SQLite 中该 thread。
                # 留意：这是闭包：/new 改外层 thread 后，再调用会用新 ID。
                # lambda 在这里定义一个小函数；函数被调用时才计算冒号后面的表达式。
                return Session(root, thread, saver, None,
                               Trace(data / "traces" / f"interactive-{thread}.jsonl", ui, secrets=lambda: [config.api_key], thread_id=thread),
                               config.max_steps, config.context_char_limit)

            session = open_session()
            verify = session.snapshot().values.get("verify_argv", [])
            ui.console.print("ForgeCode", style="bold cyan")
            ui.console.print(f"{root}\nModel: {config.model or 'not configured'} | Session: {thread}", markup=False)
            ui.console.print("Edits affect this directory and require approval. /help for commands.", style="dim")
            if session.snapshot().values:
                show_history(ui.console, session.snapshot().values.get("messages", []), recent=True)
                if session.snapshot().next:
                    ui.console.print("This conversation has an interrupted turn. /resume to continue.", style="yellow")

            # 【函数框架 main.approve】展示具体动作，读取明确审批回答。
            # 输入：interrupt 提供的 request。 输出：用户输入 y 时 True，否则 False。
            # 1. 先停止流式界面
            # 2. 普通文本显示审批 JSON
            # 3. 读取输入并做小写精确比较
            def approve(request):
                # 小例子：只对当前 app.py 替换说 y，不能授权下一张命令。
                # 留意：没有把用户回答发送给别人；只解除或拒绝本地该动作暂停。
                ui.close()
                # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
                ui.console.print(json.dumps(request, ensure_ascii=False, indent=2), markup=False)
                return input("Approve? [y/N] ").strip().lower() == "y"

            # 菜单与任务两条支路：斜杠命令改配置或看历史；普通任务才创建 Model 并 Session.turn；每轮
            # finally 关闭模型。
            while True:
                try:
                    text = input("\nforge> ").strip()
                    if not text:
                        continue
                    # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
                    command, _, value = text.partition(" ")
                    value = value.strip()
                    if command in {"/exit", "/quit"}:
                        break
                    if command == "/help":
                        ui.console.print(HELP, markup=False)
                    elif command == "/status":
                        ui.console.print(f"Session: {thread}\nModel: {config.model}\n"
                                         f"Effort: {config.reasoning_effort or 'default'}\n"
                                         f"Verify: {shlex.join(verify) or 'off'}", markup=False)
                    elif command == "/history":
                        if value not in {"", "all"}:
                            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                            raise ValueError("Use /history or /history all")
                        show_history(ui.console, session.snapshot().values.get("messages", []), value == "all")
                    elif command == "/sessions":
                        selected = select_session(ui.console, list_sessions(saver, root), thread)
                        if selected:
                            thread = selected
                            session = open_session()
                            snapshot = session.snapshot()
                            verify = snapshot.values.get("verify_argv", [])
                            ui.console.print(f"Session: {thread}", markup=False)
                            show_history(ui.console, snapshot.values.get("messages", []), recent=True)
                            if snapshot.next:
                                ui.console.print("Interrupted turn: use /resume.", style="yellow")
                    elif command == "/new":
                        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
                        thread = uuid4().hex[:12]
                        session = open_session()
                        ui.console.print(f"Session: {thread}", markup=False)
                    elif command == "/verify":
                        if not value:
                            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                            raise ValueError("Use /verify COMMAND or /verify off")
                        # 条件表达式：条件 value == 'off' 成立取前面的值，否则取 else 后面的值。
                        verify = [] if value == "off" else shlex.split(value)
                        ui.console.print(f"Verification: {shlex.join(verify) or 'off'}", markup=False)
                    elif command == "/save":
                        save(config, profile)
                        ui.console.print(f"Saved: {profile}", markup=False)
                    elif command in {"/model", "/effort", "/url", "/key", "/config", "/api"}:
                        if command == "/model":
                            config.model = value or pick_model(config, ui.console)
                        elif command == "/effort":
                            # 条件表达式：条件 value 成立取前面的值，否则取 else 后面的值。
                            # 条件表达式：条件 value == 'default' 成立取前面的值，否则取 else 后面的值。
                            config.reasoning_effort = (None if value == "default" else value) if value else pick_effort(config, ui.console)
                        elif command == "/url":
                            config.base_url = value or input("Base URL: ").strip() or config.base_url
                        elif command == "/key":
                            config.api_key = getpass("API key: ")
                        else:
                            api_menu(config, ui.console, profile)
                        ui.console.print(f"Model: {config.model}; configuration updated", markup=False)
                    elif command.startswith("/") and command != "/resume":
                        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                        raise ValueError("Unknown command; use /help")
                    else:
                        if not config.model:
                            ui.console.print("Model not configured. Use /api or /model.", style="yellow")
                            continue
                        session.runtime.model = Model(config, session.runtime.cancel)
                        # try 中发生匹配的异常会走 except；finally 无论成功、异常或取消都执行，用于收尾。
                        try:
                            session.turn(text, verify, approve, resume=command == "/resume")
                        finally:
                            session.runtime.model.close()
                except EOFError:
                    break
                except KeyboardInterrupt:
                    ui.close()
                    ui.console.print("Interrupted. /resume to continue, /new to reset, /exit to quit.")
                except Exception as exc:
                    ui.close()
                    # Provider errors can echo endpoint details; keep credentials out of terminal logs.
                    # 条件表达式：条件 isinstance(exc, ValueError) 成立取前面的值，否则取 else 后面的值。
                    message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__ + ": request failed; /resume to retry"
                    if config.api_key:
                        # replace： 得到替换后的新文字；单独调用文字 replace 还没有把结果写回磁盘。
                        message = message.replace(config.api_key, "[redacted]")
                    ui.console.print(message, style="red", markup=False)
    # 启动异常与最后关屏：内部一轮的异常已在循环里处理；这里处理启动/外层失败。密钥避免回显，
    # Terminal 最终关闭。
    except (Exception, KeyboardInterrupt) as exc:
        ui.console.print(f"Startup failed: {type(exc).__name__}", style="red")
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise SystemExit(1)
    finally:
        ui.close()


if __name__ == "__main__":
    main()
