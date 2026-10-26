"""入口组装组件；所有使用方式返回同一 Runtime。"""
# 【模块框架】装配台：把各模块装成一个 Agent，所有入口共享它。
# 连接位置：chat/run/learn/eval → create_runtime → Runtime
# 流过这里的数据：组件使用相同 root、CancelToken、ResultStore、Policy 与 emit。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from forgecode.agent.runtime import Runtime
from forgecode.context.builder import ContextBuilder
from forgecode.execution.cancel import CancelToken
from forgecode.execution.policy import Policy
from forgecode.tools.registry import ToolRegistry

# 【函数框架 create_runtime】集中组装运行核心，避免入口各写一套能力。
# 输入：仓库 root、模型、事件接收器、模块开关与执行模式。 输出：一个装配好的 Runtime。
# 1. 创建共同取消令牌并接给模型
# 2. 按模式与开关创建 ToolRegistry
# 3. 创建 ContextBuilder 并返回 Runtime
def create_runtime(root, model, emit=lambda event: None, *, planning=True, repo_context=True,
                   memory=True, store_root=None, mode="task", context_limit=32000,
                   protected_paths=()):
    # 小例子：课程传 ScriptModel；真实任务传 Model；执行链保持一致。
    # 留意：memory=False 是能力开关；mode='interactive' 控制审批，两者用途不同。
    cancel = CancelToken()
    # hasattr： 确认属性是否存在；常用来适配不同库对象是否支持 close 等方法。
    if hasattr(model, "cancel"):
        model.cancel = cancel
    tools = ToolRegistry(root, repo_context=repo_context, memory=memory, store_root=store_root,
                         policy=Policy(mode), cancel=cancel, emit=emit, protected_paths=protected_paths)
    return Runtime(model, tools, emit, planning, ContextBuilder(context_limit, tools.results))
