"""计划生成沿用统一模型接口，并接收真实仓库与基线证据。"""
# 【模块框架】路线板的简短适配器：只负责把计划请求送给模型。
# 连接位置：Runtime.plan → generate → ContextBuilder.build → Model.complete
# 流过这里的数据：phase='plan' 使用同一个上下文构建器，tools=[] 不提供动作菜单。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
# 【函数框架 generate】生成一个只含计划的模型回复。
# 输入：model、builder、state、evidence、emit。 输出：AIMessage；计划内容由 Runtime.plan
# 校验和保存。
# 1. 按 plan 模式构建消息
# 2. 记录实际请求视图
# 3. 用空工具列表调用 model.complete
def generate(model, builder, state, evidence, emit):
    # 小例子：返回三步文本，不增加 pending，也不修改 app.py。
    # 留意：没有 tools 不等于服务绝不会返回非法调用，Runtime.plan 仍要校验。
    # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
    messages, stats = builder.build(state, evidence, phase="plan")
    # 字典里的 ** 把 stats 的字段展开进来；后面的同名字段覆盖前面的字段。
    emit({"type": "request", "phase": "plan", "messages": builder.view(messages), "tools": [], **stats})
    return model.complete(messages, [], emit)
