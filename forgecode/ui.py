"""Presentation consumes events; agent cores never import Rich."""
# 【模块框架】显示屏：消费事件显示文本，不参与 Agent 决策。
# 连接位置：Trace.sink → Terminal.__call__ → Rich 展示
# 流过这里的数据：status/live/text 只用于终端显示，Agent 核心不依赖 Rich。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown


# 【类框架 Terminal】终端显示层：画菜单、读输入、展示事件。画面本身不替模型决定行动。
class Terminal:
    # 【函数框架 Terminal.__init__】准备终端显示器和流式展示状态。
    # 输入：无外部输入。 输出：Terminal 对象。
    # 1. 创建 Console
    # 2. 清空 spinner/live
    # 3. text 置空、streaming=False
    def __init__(self):
        # 小例子：入口将 Terminal 作为 Trace 的 sink。
        # 留意：UI 的 text 不是 State.messages，不能用它恢复会话。
        self.console = Console()
        self.status = None
        self.streaming = False
        self.live = None
        self.text = ""

    # 【函数框架 Terminal.__call__】按事件类型展示正在发生的过程。
    # 输入：脱敏事件 event。 输出：终端输出/流式屏幕更新。
    # 1. 模型开始时启 spinner
    # 2. token 到达时累计文字，终端用 Live，管道直接打印
    # 3. 模型结束关闭展示
    # 4. 工具、验证、finish 各用对应摘要展示
    def __call__(self, event):
        # 小例子：verification 事件真实 passed=False，屏幕显示 FAIL。
        # 留意：它展示的是公开文本和工具事实，不是完整隐藏推理；屏幕预览可能截断，日志与原文回读另
        # 存。
        kind = event["type"]
        if kind == "model_start":
            self.close()
            self.text = ""
            self.status = self.console.status("[cyan]Thinking...", spinner="dots")
            self.status.start()
        elif kind == "token":
            if self.status:
                self.status.stop()
                self.status = None
            self.text += event["text"]
            if self.console.is_terminal:
                # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
                if self.live is None:
                    self.live = Live(console=self.console, refresh_per_second=12,
                                     vertical_overflow="visible")
                    self.live.start()
                self.live.update(Markdown(self.text))
            else:
                self.console.print(event["text"], end="", markup=False, highlight=False)
            self.streaming = True
        elif kind == "model_end":
            self.close()
            if self.streaming:
                self.console.print()
                self.streaming = False
        elif kind == "tool_start":
            self.console.print(f"  > {event['name']}", style="cyan", markup=False)
        elif kind == "tool_end":
            # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
            self.console.print(event["output"][:600], style="dim", markup=False)
        elif kind == "verification":
            passed = event.get("passed", event["exit_code"] == 0 and not event["timed_out"] and not event.get("cancelled"))
            # 条件表达式：条件 passed 成立取前面的值，否则取 else 后面的值。
            self.console.print(f"{event['phase']} verification: {'PASS' if passed else 'FAIL'}",
                               style="green" if passed else "red", markup=False)
            # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
            self.console.print(event["output"][:1800], style="dim", markup=False)
        elif kind == "finish":
            self.console.print(f"{event['status']} | steps={event.get('steps', 0)} | "
                               f"tools={event.get('tool_calls', 0)} | tokens={event.get('tokens', 0)}",
                               style="cyan", markup=False)
        elif kind == "error":
            self.close()
            self.console.print(event["message"], style="red", markup=False)

    # 【函数框架 Terminal.close】关闭仍在运行的流式区域和等待指示器。
    # 输入：当前 live/status。 输出：资源停止并置空。
    # 1. 有 Live 就 stop
    # 2. 有 status 就 stop
    # 3. 清空引用，允许重复调用
    def close(self):
        # 小例子：审批前 close，避免提示被流式输出覆盖。
        # 留意：finally 收尾需要可重复关闭，不能假设每次都已收到 model_end。
        if self.live:
            self.live.stop()
            self.live = None
        if self.status:
            self.status.stop()
            self.status = None
