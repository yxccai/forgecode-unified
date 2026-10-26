"""主阅读入口：同一组节点服务聊天、任务、教学和评测。
读顺序：act → tools_node → route_tools → verify；再读 plan、baseline、finish。
"""
# 【模块框架】修理厂的总调度：决定下一站，自己不充当模型，也不直接读写源码。
# 连接位置：app.create_runtime → Runtime → graph 的各节点 → 工具/验证器
# 流过这里的数据：输入 State；节点返回一小份更新字典。图负责把更新合并回 State，其中
# messages 使用追加规则。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import json
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import interrupt
from forgecode.agent import planning
from forgecode.agent.state import budget, usage_update
from forgecode.context.builder import ContextBuilder
from forgecode.execution.cancel import Cancelled
from forgecode.tools.results import ToolResult
from forgecode.verification import Verification

# 【类框架 Runtime】运行协调器：持有模型、工具、上下文和验证器。方法返回进度更新，图负责合
# 并与调度。
class Runtime:
    # 【函数框架 Runtime.__init__】把模型、工具、上下文和验证器接到同一位调度员身上。
    # 输入：model 负责回复；tools 负责动作；emit 接收事件；planning 是规划开关。 输出：一个
    # Runtime 对象。构造时不会向模型发请求。
    # 1. 保存传入的能力对象
    # 2. 让上下文使用工具的结果仓库
    # 3. 共用取消信号，并创建验证器
    def __init__(self, model, tools, emit=lambda event: None, planning=True, context=None):
        # 小例子：create_runtime(root, ScriptModel(...)) 也走这里，离线课没有另一套调度员。
        # 留意：self.context.result_store 指向同一个结果仓库，否则裁剪后的编号可能无法回读。
        # 装配依赖：右边的三个对象分别存到当前 Runtime 上。以后 self.model 找到模型，self.tools 找
        # 到工具；这里还没有执行任务。
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        self.model, self.tools, self.emit = model, tools, emit
        self.planning = planning
        self.context = context or ContextBuilder()
        self.context.result_store = tools.results
        self.cancel = tools.cancel
        self.verifier = Verification(tools.root, self.cancel)

    # 【函数框架 Runtime._emit】给事件贴上这次任务的编号和当前请求次数。
    # 输入：state 和一条 event 字典。 输出：调用 emit；本函数没有返回状态更新。
    # 1. 从 state 取 run_id 和 steps
    # 2. 与 event 合并
    # 3. 交给日志/界面接收器
    def _emit(self, state, event):
        # 小例子：工具结束事件会带 run_id，让你知道它属于哪个任务。
        # 留意：字典后面的同名键覆盖前面键，所以显式 event.step 可以表示完成后的次数。
        # 字典里的 ** 把 event 的字段展开进来；后面的同名字段覆盖前面的字段。
        self.emit({"run_id": state["run_id"], "step": state["steps"], **event})

    # 【函数框架 Runtime.observed_node】给一个业务节点套上前后录像，像给工位安装摄像头。
    # 输入：节点名字 name 与业务函数 fn。 输出：返回新的函数 node；此时还没有执行 fn。
    # 1. 定义内部 node 函数
    # 2. 让内部函数记住 name 和 fn
    # 3. 把函数本身返回给执行图
    def observed_node(self, name, fn):
        """节点前后打印真实状态摘要，学习视图从这些事件生成。"""
        # 小例子：graph 为 act 安装 observed_node('act', runtime.act)。
        # 留意：return node 与 return node(state) 不同：前者交出可调用的工位，后者立刻开工。
        # 【函数框架 Runtime.observed_node.node】真正执行被包裹的节点，并记录执行前后的状态摘要。
        # 输入：执行图传入的 state。 输出：原业务函数的 update 字典。
        # 1. 先记录节点开始
        # 2. 调用 fn(state) 得到局部更新
        # 3. 临时合并摘要并记录，再返回 update
        def node(state):
            # 小例子：act 返回 steps=2 后，界面能显示从 1 到 2 的变化。
            # 留意：merged 只用于观察；真正的消息追加由图的 reducer 完成，不能把临时字典当完整数据库快
            # 照。
            self._emit(state, {"type": "node", "node": name, "phase": "start", "state": self.summary(state)})
            update = fn(state)
            # 这份简单字典合并只给事件摘要看；真正合并消息时仍由图使用 add_messages。
            # 字典里的 ** 把 state、update 的字段展开进来；后面的同名字段覆盖前面的字段。
            merged = {**state, **update}
            self._emit(merged, {"type": "state", "node": name, "state": self.summary(merged),
                               "messages_added": len(update.get("messages", []))})
            return update
        return node

    # 【函数框架 Runtime.summary】只摘出适合展示的进度字段。
    # 输入：完整 state。 输出：包含 status、steps、calls、tokens、cursor、repairs 等的短字典。
    # 1. 列出要看的字段
    # 2. 对每个字段用 get 读取
    # 3. 组装摘要
    # @staticmethod：调用时不自动接收 self；这里仅使用显式参数。
    @staticmethod
    def summary(state):
        # 小例子：pending 有两项、cursor=1：第二张动作单还没执行。
        # 留意：摘要没有 messages，不可拿它恢复 Agent。
        # 推导式把循环写紧凑：从 ('status', 'steps', 'calls', 'tokens', 'usage_missing', 'cursor',
        #  'repairs', 'plan') 逐项取出 k，收集成字典。
        return {k: state.get(k) for k in ("status", "steps", "calls", "tokens", "usage_missing", "cursor", "repairs", "plan")}

    # 【函数框架 Runtime._failure】把异常转换成明确的停止状态。
    # 输入：state、异常 exc、可选计费更新 account。 输出：status=cancelled 或 error，加异常类
    # 别和计数更新。
    # 1. 区分取消与普通错误
    # 2. 发 failure 事件
    # 3. 返回停止状态并保留已发生的请求计数
    def _failure(self, state, exc, account=None):
        # 小例子：模型已被调用但断网：steps 仍增加，用量记未知。
        # 留意：日志只记异常类别，避免服务端异常回显密钥；失败请求也可能产生费用。
        # 条件表达式：条件 isinstance(exc, (Cancelled, KeyboardInterrupt)) 成立取前面的值，否则取
        # else 后面的值。
        code = "cancelled" if isinstance(exc, (Cancelled, KeyboardInterrupt)) else "error"
        self._emit(state, {"type": "failure", "status": code, "exception_type": type(exc).__name__})
        # 字典里的 ** 把 account or {} 的字段展开进来；后面的同名字段覆盖前面的字段。
        return {"status": code, "error": type(exc).__name__, **(account or {})}

    # 【函数框架 Runtime.failed_usage】失败时核对：请求到底发出了吗，回复用量拿到了吗。
    # 输入：attempted 布尔值、可能为空的 answer、原 state。 输出：需要补上的计数更新字典。
    # 1. 没发请求就不增加次数
    # 2. 已有回复就复用 usage_update
    # 3. 发过请求但无回复则增加 steps 和 usage_missing
    # @staticmethod：调用时不自动接收 self；这里仅使用显式参数。
    @staticmethod
    def failed_usage(state, attempted, answer):
        # 小例子：上下文构建失败 attempted=False；请求后服务断开 attempted=True。
        # 留意：这里数逻辑模型调用；SDK 内部 HTTP 重试没有逐次拆开统计。
        if not attempted:
            return {}
        # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
        if answer is not None:
            return usage_update(state, answer)
        return {"steps": state["steps"]+1, "usage_missing": state["usage_missing"]+1}

    # 【函数框架 Runtime.baseline】修理前先测一遍，确认原来的问题长什么样。
    # 输入：state 中的验证命令和验证开关。 输出：基线结果、状态及一条包含结果的 HumanMessage。
    # 1. 取消或没开验证就跳过
    # 2. 真实运行验证命令
    # 3. 记录并将结果加入消息，供后面的计划参考
    def baseline(self, state):
        # 小例子：原 add(2,3)=-1，测试期望 5；exit_code=1 写入 baseline。
        # 留意：基线失败通常是任务证据，不等于 Agent 已经运行失败。
        if self.cancel.cancelled:
            return {"status": "cancelled"}
        if not state["verification_enabled"]:
            return {}
        try:
            result = self.verifier.run(state["verify_argv"])
        except (OSError, ValueError, Cancelled, KeyboardInterrupt) as exc:
            return self._failure(state, exc)
        # 字典里的 ** 把 result 的字段展开进来；后面的同名字段覆盖前面的字段。
        self._emit(state, {"type": "verification", "phase": "baseline", **result})
        preview = self.tools.results.fit(ToolResult("verification", "baseline", content=result)).message()
        # 条件表达式：条件 result['cancelled'] 成立取前面的值，否则取 else 后面的值。
        return {"baseline": result, "status": "cancelled" if result["cancelled"] else "running",
                "messages": [HumanMessage("Baseline verification:\n" + preview)]}

    # 【函数框架 Runtime.plan】先取证据，再让模型提出短路线。
    # 输入：state；检索器与记忆库；planning 开关。 输出：plan 文本、evidence 和本次请求用量。
    # 1. 核对状态与取消
    # 2. 准备仓库/记忆证据，关闭规划时只保留证据
    # 3. 检查预算，请求纯文本计划并拒绝工具调用
    # 4. 记录计划与计数
    def plan(self, state):
        # 小例子：计划：读 app.py → 精确替换 → 用测试验收。
        # 留意：计划是建议，不是已完成动作；规划也消耗一次模型调用。
        # 只对仍运行的任务规划：先查终态和取消；不能在错误或取消后继续拨电话。
        if state["status"] != "running":
            return {}
        if self.cancel.cancelled:
            return {"status": "cancelled"}
        # 准备失败统计与嵌套回调：内部 emit 用 nonlocal 改 attempted；规划请求失败时知道是否已拨号。
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        attempted, answer = False, None
        # 【函数框架 Runtime.plan.emit】观察计划生成是否已经发出了模型请求。
        # 输入：planning.generate 发出的 event。 输出：转发事件，并可能修改外层 attempted。
        # 1. 检查 event.type 是否为 request
        # 2. 用 nonlocal 改外层 attempted
        # 3. 加任务编号后转发
        def emit(event):
            # 小例子：发 request 后断网，外层知道该把这次计入 steps。
            # 留意：nonlocal 指外层函数变量；没有它，赋值只会创建局部变量，失败计数就会失真。
            # nonlocal 指向外层函数的变量，下面的赋值会改外层那一个，而不是新建同名局部变量。
            nonlocal attempted
            if event.get("type") == "request":
                attempted = True
            self._emit(state, event)
        # 先准备仓库和来源有效的记忆：关闭规划时仍返回这些证据，只是不发计划请求。预算耗尽则停止，
        # 不偷偷免费生成计划。
        evidence = self.context.evidence(self.tools, state["task"], emit)
        if not self.planning:
            return {"plan": "", "evidence": evidence}
        if budget(state):
            return {"status": "budget_exhausted", "evidence": evidence}
        # 用同一模型接口要纯文本路线：generate 用 plan 模式和空工具菜单。返回任何工具请求都非法，
        # 因为计划还没到行动工位。
        try:
            # 先请求公开计划：规划请求不带工具菜单；返回工具调用会被拒绝。计划文字是建议，还没有执行其
            # 中步骤。
            answer = planning.generate(self.model, self.context, state, evidence, emit)
            if answer.invalid_tool_calls or answer.tool_calls:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Planning must return a plain proposed plan")
        except (Exception, KeyboardInterrupt) as exc:
            return self._failure(state, exc, self.failed_usage(state, attempted, answer))
        # 保存公开提案与实际代价：usage_update 加请求数；只保留计划前4000字符供后续请求。计划是提
        # 案，观察可以推翻。
        self._emit(state, {"type": "plan", "text": str(answer.content)})
        # 保存计划和计数：把公开文字截到最多 4000 字符，连同证据写回进度；请求已发生，要计一次。
        # 字典里的 ** 把 usage_update(state, answer) 的字段展开进来；后面的同名字段覆盖前面的字段。
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        return {
            **usage_update(state, answer),
            "plan": str(answer.content)[:4000],
            "evidence": evidence
        }

    # 【函数框架 Runtime.act】问模型下一步，把建议放入待办，不在这里执行动作。
    # 输入：当前 state，包括 task、messages、预算与状态。 输出：新增 AIMessage、pending、
    # cursor=0、新状态和计数。
    # 1. 先检查状态、取消与预算
    # 2. 重新核对证据并组装本次请求
    # 3. 调用模型并校验工具调用 ID/参数解析
    # 4. 把回复存入消息；有动作则 running，无动作则 answered
    def act(self, state):
        """问模型一次；工具请求进入 pending，程序尚未执行它们。"""
        # 小例子：模型回复 read_file(id='read')，此刻 app.py 仍是 a-b。
        # 留意：模型说“改好了”只代表 answered；真实文件变化在 tools_node，verified 还要经过测试。
        # 先确认能继续：只有 running 或 answered 的任务可以走这条请求路径；其他状态直接交回空更新。
        if state["status"] not in {"running", "answered"}:
            return {}
        # 检查停止信号：如果已经取消，就返回 cancelled；后面的模型请求不会发生。
        if self.cancel.cancelled:
            return {"status": "cancelled"}
        # 检查次数与用量上限：budget 读取当前计数。达到限制就停止，避免无限循环；具体条件见 State
        # 文件中的 budget 函数。
        if budget(state):
            return {"status": "budget_exhausted"}
        # 准备事件记录员：emit 是一个转发函数：收到事件时补上这次任务的身份。这一步不会决定下一站。
        # lambda 在这里定义一个小函数；函数被调用时才计算冒号后面的表达式。
        emit = lambda event: self._emit(state, event)
        # 先区分“没发请求”和“发了但没拿到回复”；失败时要据此正确计数。
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        attempted, answer = False, None
        try:
            # 文件会随工具执行而变化；每次请求重新核对来源，旧记忆不能持续冒充有效证据。
            # 找当前相关材料：evidence 放本轮查到的仓库和有效记忆材料。工具可能改过文件，所以每轮重新
            # 查。
            evidence = self.context.evidence(self.tools, state["task"], emit)
            # 组织本轮输入：build 返回两样东西：messages 是给模型的消息，stats 是容量统计。逗号让两个
            # 名字分别接住返回值。
            # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
            messages, stats = self.context.build(state, evidence)
            # 字典里的 ** 把 stats 的字段展开进来；后面的同名字段覆盖前面的字段。
            # 推导式把循环写紧凑：从 self.tools.items 逐项取出 t，收集成列表。
            emit({"type": "request", "phase": "act", "messages": self.context.view(messages),
                  "tools": [t.name for t in self.tools.items], **stats})
            # 现在才真正拨电话：记录 request 后标记 attempted，再 complete。工具菜单只让模型写动作单；
            # complete 返回时 app.py 不会因此改变。取消和协议检查防止拿半份/非法动作继续执行。
            attempted = True
            # 真的问模型一次：把消息和工具菜单交给模型，answer 接住回复。菜单只说明能申请什么，模型的
            # 请求还没有执行。
            answer = self.model.complete(messages, self.tools.items, emit)
            self.cancel.check()
            # 回复也要检查：参数格式错误不能放行；后面还检查每张工具单都有不重复的编号。
            if answer.invalid_tool_calls:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Provider returned malformed tool arguments")
            # 每张工具单都需要编号；结果消息随后按这个编号与请求配对。
            # 推导式把循环写紧凑：从 answer.tool_calls 逐项取出 call，收集成列表。
            ids = [call.get("id") for call in answer.tool_calls]
            # any 只要发现一个坏编号就成立；set(ids) 去重后长度变小，表示编号重复。
            # 推导式把循环写紧凑：从 ids 逐项取出 i，收集成迭代器。
            if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Tool calls require unique nonempty IDs")
        # 失败有自己的出口：请求或校验出错时，把原因与已经发生的请求计数交给 _failure，不冒充正常
        # 回答。
        except (Exception, KeyboardInterrupt) as exc:
            return self._failure(state, exc, self.failed_usage(state, attempted, answer))
        # 把建议交给待办夹：usage_update 更新调用数；messages 追加回复；pending 是动作单列表；
        # cursor=0 指第一张。无工具单才是 answered，尚未等于 verified。
        emit({"type": "decision", "step": state["steps"]+1, "tool_calls": answer.tool_calls, "content": answer.content})
        # 把回复交回图：返回的是一份进度更新：新增回复、待办工具、当前材料、下一项位置和状态。各字
        # 段按行展开，含义写在字段旁。
        # 字典里的 ** 把 usage_update(state, answer) 的字段展开进来；后面的同名字段覆盖前面的字段。
        # 条件表达式：条件 answer.tool_calls 成立取前面的值，否则取 else 后面的值。
        return {
            # ** 把 usage_update 返回的次数与用量字段摊进这个更新字典。
            **usage_update(state, answer),
            # [answer] 是只有一项的列表：本轮回复交给图，再按消息合并规则加入历史。
            "messages": [answer],
            # 点号读取模型回复的工具清单，保存为待办；保存这一项还没有执行工具。
            "pending": answer.tool_calls,
            # 左边是保存位置的名字，右边 evidence 是本轮查到的相关材料。
            "evidence": evidence,
            # 下一项下标从 0 开始，也就是待办的第一项。
            "cursor": 0,
            # 有工具请求取 running；没有则取 answered。后续由图选择工具、验证或收尾。
            "status": "running" if answer.tool_calls else "answered"
        }

    # 【函数框架 Runtime.tools_node】一次只执行 pending[cursor] 这张动作单，留下可恢复的结果。
    # 输入：pending 列表、cursor、run_id、步骤；工具柜和审批策略。 输出：一条 ToolMessage，
    # calls+1，cursor+1；必要时停止。
    # 1. 检查取消和工具预算
    # 2. 计算稳定动作编号并准备参数
    # 3. 核对旧回执，只有要真正执行才发审批
    # 4. 交给 ToolRegistry.execute
    # 5. 写回带同一个调用 ID 的结果，再推进游标
    def tools_node(self, state):
        """逐个工具校验、审批和计数；每个动作有稳定的恢复 ID。"""
        # 小例子：两个读取：cursor=0 读列表，cursor=1 读源码，cursor=2 才回 act。
        # 留意：审批恢复会重新进入节点，副作用与 checkpoint 之间可能崩溃，所以不能只靠游标去重。
        # 停止铃与工具上限先检查：calls 计的是尝试执行次数，包括被拒或失败。到上限就停止，避免模型
        # 一直开工具单。
        if self.cancel.cancelled:
            return {"status": "cancelled"}
        if state["calls"] >= state["max_calls"]:
            return {"status": "budget_exhausted"}
        # 拿出当前这一项：pending 是待办列表，cursor 是书签。一次节点只处理书签指向的一张工具单。
        call = state["pending"][state["cursor"]]
        # 给本次任务、本轮回复、当前序号和工具单组合稳定动作编号；重开仍查同一张回执。
        operation_id = self.tools.receipts.id_for(state["run_id"], state["steps"], state["cursor"], call)
        grant = None
        # 尝试规范化动作参数：prepare 能查 schema 与保护文件。准备失败仍交 execute 返回统一错误结
        # 果，保证模型收到失败观察。
        try:
            # 检查当前工具单：call 的 name 是申请的能力，args 是具体输入。prepare 核对参数合同，准备和
            # 真正执行分开进行。
            # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
            spec, args = self.tools.prepare(call["name"], call["args"])
        except (ValueError, TypeError, KeyError, PermissionError):
            # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
            spec, args = None, call["args"]
        # 先查过去是否已执行；进度存档落后时，不能因此重复动手。
        saved = self.tools.receipts.get(operation_id)
        # 没有回执通常还需要执行；已有记录则先核对效果，避免重复索取批准。
        will_execute = not saved
        if saved and not saved["result"] and call["name"] == "edit_file":
            from forgecode.execution.receipts import fingerprint
            current = fingerprint(self.tools.guard.path(args["path"]))
            # 文件仍是 before 且尚未是 after，才把它当成还没完成的编辑。
            will_execute = current == saved["expected"].get("before") and current != saved["expected"].get("after")
        # 动作前判断策略：策略可能直接允许、拒绝，或要求用户批准。批准必须匹配本次具体动作。
        if spec and self.tools.policy.decision(spec.risk) == "ask" and will_execute:
            request = self.tools.policy.request(self.tools.root, call["name"], args, call["id"], spec.risk)
            # 字典里的 ** 把 request 的字段展开进来；后面的同名字段覆盖前面的字段。
            self._emit(state, {"type": "approval_request", **request})
            # 图在这里暂停。继续时从外部拿到决定，随后再把批准绑定到本次具体参数。
            decision = interrupt(request)
            grant = self.tools.policy.grant(request, decision)
            self._emit(state, {"type": "approval", "approved": grant.allowed, "call_id": call["id"]})
        # 经过统一通道实际动手：execute 再次校验权限与回执，然后才调用函数。KeyboardInterrupt 转成
        # 取消结果，不能只把终端关掉就认为进程已经退出。
        self._emit(state, {"type": "tool_start", "name": call["name"], "args": call["args"], "tool_call_id": call["id"]})
        try:
            # 交给统一执行器：真实副作用发生在统一执行路径里；这个调用返回结构化结果，后面还要写进历史。
            result = self.tools.execute(call["name"], call["args"], call_id=call["id"], grant=grant, operation_id=operation_id)
        except KeyboardInterrupt:
            self.cancel.cancel()
            result = ToolResult.error(call["name"], call["id"], "cancelled", "Execution interrupted", "cancelled")
        # 把观察装回历史，移动书签：ToolMessage 的 tool_call_id 必须与 AI 动作 ID 一致。cursor+1
        # 只表示这一项已有结果；uncertain 要 needs_review，不能当一般失败继续蒙。
        self._emit(state, {"type": "tool_end", "name": call["name"], "tool_call_id": call["id"],
                           "output": result.message(), "result_status": result.status, "duration_ms": result.duration_ms})
        # 交回回单并移书签：ToolMessage 带原工具单的 id。calls 加一次尝试，cursor 加 1；图合并并保
        # 存这些进度。
        update = {"messages": [ToolMessage(content=result.message(), tool_call_id=call["id"])],
                  "calls": state["calls"]+1, "cursor": state["cursor"]+1}
        if result.status in {"cancelled", "uncertain"}:
            # 条件表达式：条件 result.status == 'cancelled' 成立取前面的值，否则取 else 后面的值。
            update["status"] = "cancelled" if result.status == "cancelled" else "needs_review"
        return update

    # 【函数框架 Runtime.verify】让真实测试决定通过，或产生下一轮回修的证据。
    # 输入：verify_argv、repairs、max_repairs、预算。 输出：verified、verification_failed、
    # cancelled，或 running 加失败反馈。
    # 1. 运行预授权测试
    # 2. 通过则标记 verified
    # 3. 达到回修/预算上限则停止
    # 4. 否则写入事实、未确定假设和下一动作，回 act
    def verify(self, state):
        """验证失败进入下一轮；成功状态由程序根据命令结果决定。"""
        # 小例子：a*b 得 6，测试要 5；repairs 从 0 到 1，模型看到失败输出。
        # 留意：反馈里的 hypothesis 明确未确定，不把程序猜测冒充根因。
        # 取消先于质检：拉响停止铃就不再开测试进程。异常交 _failure 转成终态。
        if self.cancel.cancelled:
            return {"status": "cancelled"}
        try:
            result = self.verifier.run(state["verify_argv"])
        except (OSError, ValueError, Cancelled, KeyboardInterrupt) as exc:
            return self._failure(state, exc)
        # 记录真实报告，处理取消/通过：取消并非测试成功；passed 来自 Verification.run 的退出码、超
        # 时和取消核验。
        # 字典里的 ** 把 result 的字段展开进来；后面的同名字段覆盖前面的字段。
        self._emit(state, {"type": "verification", "phase": "final", **result})
        if result["cancelled"]:
            return {"verification": result, "status": "cancelled"}
        # 通过来自测试事实：只有 Verification.run 给出 passed=True，才写入 verified。此处判断的是
        # 实际测试结果，而不是模型回答里有没有“完成”两个字。
        if result["passed"]:
            return {"verification": result, "status": "verified"}
        # 失败后先问还有没有修理机会：上限或预算已到则 verification_failed；不能无限回 act。
        if state["repairs"] >= state["max_repairs"] or budget(state):
            return {"verification": result, "status": "verification_failed"}
        # 把确定事实与待定解释分开：fact=测试失败，hypothesis 明确尚未建立，next_action 要先检查证
        # 据再修。
        feedback = {"attempt": state["repairs"]+1, "fact": "Trusted verification failed",
                    "exit_code": result["exit_code"], "timed_out": result["timed_out"],
                    "hypothesis": "Not yet established; infer from the output",
                    "next_action": "Inspect the evidence and repair before claiming completion"}
        # 字典里的 ** 把 feedback 的字段展开进来；后面的同名字段覆盖前面的字段。
        self._emit(state, {"type": "feedback", **feedback})
        # 失败报告再进入输入：长测试输出同样经过 ResultStore。repairs+1，状态重新 running，
        # route_verify 才会回 act。
        preview = self.tools.results.fit(ToolResult("verification", "final", content=result)).message()
        # 失败反馈回到下一轮：还有回修机会时保存失败事实，repairs 加 1，把失败输出放进消息。图随后
        # 重新调度 act。
        # 这里的单星号把列表/序列中的各项展开；原顺序保留。
        return {
            "verification": result,
            "status": "running",
            "repairs": state["repairs"]+1,
            "feedback": [*state["feedback"], feedback],
            "messages": [HumanMessage("Verification failed. Analyze the evidence, repair and verify again.\n" + preview)]
        }

    # 【函数框架 Runtime.route_act】模型回完后选择下一站。
    # 输入：state.status 与 verification_enabled。 输出：字符串 tools、verify 或 finish。
    # 1. running 表示有待办，去 tools
    # 2. answered 且有验证命令，去 verify
    # 3. 其他状态去 finish
    def route_act(self, state):
        # 小例子：answered + 验证开启 → verify；预算耗尽 → finish。
        # 留意：返回的是节点名称，路由函数不执行该节点。
        # 有工具请求，去工具节点：return "tools" 交回一个节点名字。真正调用 tools_node 的是图，
        # route_act 自己不执行工具。
        if state["status"] == "running":
            return "tools"
        # 条件表达式：条件 state['status'] == 'answered' and state['verification_enabled'] 成立取
        # 前面的值，否则取 else 后面的值。
        return "verify" if state["status"] == "answered" and state["verification_enabled"] else "finish"

    # 【函数框架 Runtime.route_tools】决定继续执行待办，还是再问一次模型。
    # 输入：status、cursor、pending 长度。 输出：tools、act 或 finish。
    # 1. 已停止则收尾
    # 2. cursor 小于待办长度则继续 tools
    # 3. 待办全部执行后回 act
    def route_tools(self, state):
        # 小例子：pending 长度 2，cursor=1 → tools；cursor=2 → act。
        # 留意：循环的是每一张动作单，不能漏掉同一回复里的第二个动作。
        if state["status"] != "running":
            return "finish"
        # 条件表达式：条件 state['cursor'] < len(state['pending']) 成立取前面的值，否则取 else 后
        # 面的值。
        return "tools" if state["cursor"] < len(state["pending"]) else "act"

    # 【函数框架 Runtime.route_verify】把失败回修接回行动，把最终结果接到收尾。
    # 输入：验证节点更新后的 status。 输出：act 或 finish。
    # 1. 检查是否仍为 running
    # 2. running 回 act
    # 3. 其余状态 finish
    def route_verify(self, state):
        # 小例子：第一次测试失败且未超限 → act；第二次通过 → finish。
        # 留意：running 在这里意味着允许再修，不能误认已经完成。
        # 条件表达式：条件 state['status'] == 'running' 成立取前面的值，否则取 else 后面的值。
        return "act" if state["status"] == "running" else "finish"

    # 【函数框架 Runtime.finish】关门前补齐结果、收集差异，并保存有验证依据的经验。
    # 输入：最终 state、剩余 pending、工具柜与记忆库。 输出：未执行动作的明确失败消息；同时发
    # diff 和 finish 事件。
    # 1. 给剩余动作补 not_executed 结果，保持消息配对
    # 2. 取消时跳过 diff，否则读取真实差异
    # 3. 仅 verified 时保存来源关联的任务经验
    # 4. 发完成统计，缺失用量时总 tokens 留空
    def finish(self, state):
        """交付状态与 diff；未执行的请求补明确结果，避免悬空工具消息。"""
        # 小例子：最终 add=a+b，经验带 passed=true 和验证命令，而不是单凭模型说成功。
        # 留意：收尾不会把失败变成功；保存经验也不是替代码做无限期正确性保证。
        # 剩余动作也有明确结果：预算/取消中途停止时，未执行单补 not_executed ToolMessage，避免历史
        # 留下不闭合的工具请求。
        updates = {}
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        pending = state["pending"][state["cursor"]:]
        if pending:
            # 推导式把循环写紧凑：从 pending 逐项取出 c，收集成列表。
            updates["messages"] = [ToolMessage(content=ToolResult.error(c["name"], c["id"], "not_executed",
                                      "Task stopped before this action: " + state["status"]).message(), tool_call_id=c["id"]) for c in pending]
            updates["cursor"] = len(state["pending"])
        # 读取事实，不伪造差异：取消后不再额外执行 Git；其他终点取实际差异。
        # 条件表达式：条件 state['status'] != 'cancelled' 成立取前面的值，否则取 else 后面的值。
        diff = self.tools.git_diff() if state["status"] != "cancelled" else {"status": "not_collected_after_cancel"}
        # 仅真实验证通过才保存验证经历：选最多10个改变且存在的来源，记录 passed 与 argv。保存失败
        # 只发 skipped，不倒推成功为失败，也不生成虚假经验。
        if state["status"] == "verified" and self.tools.memory:
            from forgecode.execution.workspace import git
            try:
                changed = git(self.tools.root, "diff", "--name-only", "HEAD").splitlines()
                # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
                # 推导式把循环写紧凑：从 changed 逐项取出 p，仅保留满足 self.tools.guard.path(p).is_file()
                #  的项，收集成列表。
                sources = [p for p in changed if self.tools.guard.path(p).is_file()][:10]
                if sources:
                    # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
                    self.tools.memory.remember("Verified task: " + state["task"][:1600], sources, kind="episodic",
                                               confidence=0.8, evidence={"passed": True, "argv": state["verify_argv"]})
            except (OSError, ValueError, RuntimeError):
                self._emit(state, {"type": "memory_save", "status": "skipped"})
        # 按已知和未知分别报账：任何 usage_missing 都让最终 total tokens 留 None；known_tokens 小
        # 计和未知次数仍保留。
        self._emit(state, {"type": "diff", "output": json.dumps(diff, ensure_ascii=False)})
        # 条件表达式：条件 not state['usage_missing'] 成立取前面的值，否则取 else 后面的值。
        self._emit(state, {"type": "finish", "status": state["status"], "steps": state["steps"],
                          "tool_calls": state["calls"], "tokens": state["tokens"] if not state["usage_missing"] else None,
                          "known_tokens": state["tokens"], "usage_missing": state["usage_missing"]})
        return updates

    # 【函数框架 Runtime.build】把当前调度员接到唯一执行图。
    # 输入：checkpoint saver 与是否每个工具后暂停。 输出：已编译的图。
    # 1. 延迟导入 graph.build
    # 2. 传入当前 Runtime 和 saver
    # 3. 返回图供入口 invoke
    def build(self, saver, pause_after_tool=False):
        # 小例子：聊天和离线 demo 都调用 runtime.build(saver)。
        # 留意：Runtime 负责业务，graph 负责连线，二者不要混成两套 Agent。
        from forgecode.agent.graph import build
        return build(self, saver, pause_after_tool)
