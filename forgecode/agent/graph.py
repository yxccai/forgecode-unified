"""唯一运行图：这里连接节点；节点业务与路由在 Runtime。"""
# 【模块框架】工厂线路图：六个工位怎么连、哪些地方能回头。
# 连接位置：Runtime.build → graph.build → graph.invoke → 节点与路由
# 流过这里的数据：State 是图的状态类型，saver 是存档对象，节点业务来自 Runtime。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from langgraph.graph import END, START, StateGraph
from forgecode.agent.state import State

# 【函数框架 build】建立一张有分支和回路的执行图。
# 输入：runtime、saver、pause_after_tool。 输出：可调用的编译图。
# 1. 注册 baseline/plan/act/tools/verify/finish 六个节点
# 2. 连固定边与三组条件边
# 3. 绑定 checkpoint，按需在工具后暂停
def build(runtime, saver, pause_after_tool=False):
    # 小例子：verify 失败但允许回修时，route_verify 返回 act，形成反馈回路。
    # 留意：START 和 END 是框架标记；顺序是 baseline 在 plan 前，规划能看到原始失败。
    # 建立调度图：State 告诉图进度有哪些字段、消息怎样合并。图随后登记节点，再按连线运行。
    graph = StateGraph(State)
    # 登记名字与方法：runtime.act 没有括号：传过去的是方法，登记时不执行。observed_node 为每次
    # 运行补事件记录。
    for name, fn in (("baseline", runtime.baseline), ("plan", runtime.plan), ("act", runtime.act),
                     ("tools", runtime.tools_node), ("verify", runtime.verify), ("finish", runtime.finish)):
        # add_node： 把名字与可调用方法登记为节点；登记不等于现在运行该方法。
        graph.add_node(name, runtime.observed_node(name, fn))
    # 基线先运行，让可选计划看得到实际失败证据。
    # 先测原状，再计划和行动：固定连线明确 baseline → plan → act。是否实际测试、是否实际规划由
    # 各节点读取开关决定。
    graph.add_edge(START, "baseline")
    graph.add_edge("baseline", "plan")
    graph.add_edge("plan", "act")
    # 根据状态选择道路：act 结束后图调用 route_act 取得节点名，再调度那个节点。另两条条件连线
    # 负责工具接力和验证回修。
    graph.add_conditional_edges("act", runtime.route_act, ["tools", "verify", "finish"])
    # add_conditional_edges： 登记路由函数；节点结束并合并进度后，图才调用它决定下一站。
    graph.add_conditional_edges("tools", runtime.route_tools, ["tools", "act", "finish"])
    # add_conditional_edges： 登记路由函数；节点结束并合并进度后，图才调用它决定下一站。
    graph.add_conditional_edges("verify", runtime.route_verify, ["act", "finish"])
    graph.add_edge("finish", END)
    # 绑定存档与暂停位置：saver 是进度保存组件。相同会话身份才能接上对应存档；存进度本身不能撤
    # 销磁盘动作。
    # 条件表达式：条件 pause_after_tool 成立取前面的值，否则取 else 后面的值。
    return graph.compile(checkpointer=saver, interrupt_after=["tools"] if pause_after_tool else [])
