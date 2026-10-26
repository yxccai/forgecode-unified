"""会话是 Runtime 的使用者；不会通过子类再实现工具节点。"""
# 【模块框架】对话接待员：用同一张执行图接新一轮、继续暂停或重开存档。
# 连接位置：interactive.main → Session.turn → graph.invoke → Runtime
# 流过这里的数据：thread_id 区分存档；run_id 区分任务轮次；连续对话只追加新用户消息。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from pathlib import Path
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from forgecode.app import create_runtime
from forgecode.agent.state import initial

# 【类框架 Session】一次会话的进度协调器：创建/读取 State → invoke 图 → 处理暂停 → 保存与
# 展示历史。
class Session:
    # 【函数框架 Session.__init__】为一个对话建立运行图与存档选项。
    # 输入：root、thread、saver、model、emit、步数和上下文上限。 输出：Session，包含 Runtime
    # 与 graph。
    # 1. 固定仓库根和 thread 配置
    # 2. 以 interactive 模式装配、默认不做独立规划
    # 3. 编译带 saver 的图，保存每轮上限
    def __init__(self, root, thread, saver, model, emit, max_steps=20, context_limit=32000):
        # 小例子：thread='abc' 对应数据库中这段对话的 checkpoint。
        # 留意：会话没有继承 Runtime 重写工具节点，只组合调用正式核心。
        # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
        self.root = Path(root).resolve()
        self.options = {"configurable": {"thread_id": thread}, "recursion_limit": 1000}
        self.runtime = create_runtime(self.root, model, emit, mode="interactive",
                                      planning=False, context_limit=context_limit)
        self.graph = self.runtime.build(saver)
        self.max_steps = max_steps

    # 【函数框架 Session.snapshot】读取这段对话当前的存档状态。
    # 输入：self.options 中的 thread_id。 输出：图的 StateSnapshot。
    # 1. 调用 graph.get_state
    # 2. 按同一 thread 查找
    # 3. 返回 values、next、tasks 等
    def snapshot(self):
        # 小例子：暂停审批时 snapshot.tasks 里有 interrupt 请求。
        # 留意：next 是还待执行的节点，不是聊天正文。
        # get_state： 读取图保存的进度与下一节点；不是重新运行旧工具。
        return self.graph.get_state(self.options)

    # 【函数框架 Session.turn】核对旧存档，再启动或继续一轮，并处理审批暂停。
    # 输入：用户 text、验证 argv、approve 回调、resume 开关。 输出：本轮最终 State。
    # 1. 核对保存的 root 与 schema_version
    # 2. 恢复必须有待执行节点；新任务不能覆盖未完成任务
    # 3. 新轮重置取消，建立状态，老会话只追加用户消息
    # 4. 循环读 interrupt，用 Command(resume=批准结果) 继续
    # 5. 没有 interrupt 时返回
    def turn(self, text, verify, approve, resume=False):
        # 小例子：/resume 继续待批准编辑；普通新问题在已有合法历史后追加一条 HumanMessage。
        # 留意：恢复不是把用户任务从头再跑；旧阶段存档不兼容时明确要求新会话。
        # 先核对厂房与档案格式：同一 thread 不能拿别的 root 的状态继续；schema_version 不兼容就拒
        # 绝，不猜迁移。
        snapshot = self.snapshot()
        if snapshot.values:
            if snapshot.values["root"] != str(self.root):
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Session repository mismatch")
            if snapshot.values.get("schema_version") != 1:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Historical checkpoint requires explicit migration; start a new unified session")
        # 恢复与新任务走不同支路：恢复必须 snapshot.next 非空，payload=None 让图从保存位置继续；新
        # 任务不能盖掉正在暂停的上一轮。
        if resume:
            if not snapshot.next:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("No interrupted turn to resume")
            payload = None
        else:
            if snapshot.next:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Use /resume for the interrupted turn or /new to start over")
            if snapshot.values and snapshot.values["status"] not in {"answered", "verified", "verification_failed", "cancelled"}:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Previous turn requires review or a new session")
            # 新轮重填进度，保留对话历史：initial 重置本轮计数。老会话 payload.messages 只放新
            # HumanMessage，add_messages reducer 负责追加，避免历史重复整段塞回。
            self.runtime.cancel.reset()
            payload = initial(text, self.root, verify, max_steps=self.max_steps, verification=bool(verify))
            if snapshot.values:
                payload["messages"] = [HumanMessage(text)]
        # 反复处理审批直到本轮结束：先读保存的 interrupt，approve 回答用 Command(resume=...) 送回。
        # invoke 没 __interrupt__ 才返回；否则下一圈继续处理暂停。
        while True:
            # 推导式把循环写紧凑：从 self.snapshot().tasks 逐项取出 task；再从 task.interrupts 逐项取
            # 出 item，收集成列表。
            pending = [item for task in self.snapshot().tasks for item in task.interrupts]
            if pending:
                payload = Command(resume=approve(pending[0].value))
            # invoke： 这里才调用对象提供的执行接口；具体是模型、工具还是图，要看点号左侧的对象。
            result = self.graph.invoke(payload, self.options)
            if not result.get("__interrupt__"):
                return result
            payload = None
