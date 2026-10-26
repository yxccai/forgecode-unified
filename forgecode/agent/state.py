"""一份 State：消息、进度、预算、证据、反馈与验证状态。"""
# 【模块框架】进度夹：把任务、消息、待办、预算和验收状态放在同一处。
# 连接位置：initial → graph 合并节点更新 → checkpoint 保存 → Session 恢复
# 流过这里的数据：State 是 TypedDict 描述；messages 的 Annotated 指定 add_messages 合并，
# 其他字段按节点更新覆盖。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from pathlib import Path
from typing import Annotated, TypedDict
from uuid import uuid4
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph.message import add_messages
from forgecode.context.builder import SYSTEM

# 【类框架 State】进度字典的字段说明。TypedDict 仍按普通字典使用；total=False 允许只返回部
# 分字段。
class State(TypedDict, total=False):
    # messages：完整消息历史；add_messages 合并新增消息
    # Annotated 前半段 list[BaseMessage] 说明字段装消息列表；后半段 add_messages 是图的合并规
    # 则。节点只返回 [新回复]，图按消息编号合并进历史，不能直接用新列表覆盖全部历史。
    messages: Annotated[list[BaseMessage], add_messages]
    # schema_version：状态格式版本；不兼容时拒绝猜迁移
    schema_version: int
    # run_id：本轮任务身份；动作恢复编号的组成部分
    run_id: str
    # task：当前用户目标；构建上下文时必须保护
    # 任务放这里：task 是用户要完成的目标。冒号后面的 str 只是说明它应当是文字，声明本身没有把
    # “修好加法”填进去。
    task: str
    # root：这一轮实际工作目录的绝对路径
    root: str
    # plan：公开建议计划，允许根据观察修订
    plan: str
    # evidence：最近一次仓库与有效记忆证据
    evidence: dict
    # steps：逻辑模型请求次数，含规划和已发出的失败请求
    # 次数和容量是刹车：steps 计已发生的模型请求，calls 计工具尝试；tokens 只累计已知用量，
    # usage_missing 留下未知次数。max_ 开头的字段设置上限，后面由 budget 检查。
    steps: int
    # calls：工具尝试次数，含失败或被拒的尝试
    calls: int
    # tokens：已知响应用量的小计，不保证完整总量
    tokens: int
    # usage_missing：没有拿到 total_tokens 的请求数
    usage_missing: int
    # max_steps：模型请求次数上限
    max_steps: int
    # max_calls：工具尝试次数上限
    max_calls: int
    # max_tokens：已知 token 响应后软上限；0 为关闭
    max_tokens: int
    # pending：当前 AI 回复提出的工具调用列表
    # 待办清单和书签：pending 装模型提出的工具请求，cursor 标出下一项。假设清单为 [读文件, 改
    # 文件]，cursor=0 指向读文件；装入清单还没有做任何操作。
    pending: list[dict]
    # cursor：下一张待处理工具单的下标，从 0 开始
    cursor: int
    # status：running/answered/verified 或预算失败、取消、待检查等状态
    # 状态决定还能不能走：running 表示继续处理，answered 表示模型这轮交了回答，verified 才表示
    # 可信验证通过。不同值会让路由选不同的下一站。
    status: str
    # verify_argv：用户预授权的可信验证命令参数列表
    # 测试与失败反馈单独保存：verify_argv 保存用户认可的测试命令参数，verification 保存实际测
    # 试结果。repairs 和 max_repairs 限制回修次数；feedback 保存失败事实。
    verify_argv: list[str]
    # verification_enabled：本轮是否做 baseline/final 验证
    verification_enabled: bool
    # baseline：修改前的真实测试结果
    baseline: dict
    # verification：最近一次修改后的可信验证结果
    verification: dict
    # repairs：验证失败后已获准的回修轮数
    repairs: int
    # max_repairs：允许回修的最大轮数
    max_repairs: int
    # feedback：失败事实、待定假设与下一动作记录
    feedback: list[dict]
    # error：停止异常类别的简短记录
    error: str

# 【函数框架 initial】为一项新任务填写完整的初始进度表。
# 输入：task、root、验证命令与各项上限。 输出：可交给图的 State 字典。
# 1. 校验预算和验证命令
# 2. 生成 run_id 与初始系统/用户消息
# 3. 把计数清零、pending 置空、status 设 running
def initial(task, root, verify=None, max_steps=20, max_calls=60, max_tokens=0,
            max_repairs=3, verification=None):
    # 小例子：initial('Fix add', root, ['python','-m','unittest','discover'])。
    # 留意：TypedDict 不会替运行时检查数值，所以函数仍显式校验；max_tokens=0 代表不启用该上限。
    if max_steps < 1 or max_calls < 1 or max_tokens < 0 or max_repairs < 0:
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise ValueError("Invalid task budgets")
    verify = list(verify or [])
    # 条件表达式：条件 verification is None 成立取前面的值，否则取 else 后面的值。
    # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
    enabled = bool(verify) if verification is None else verification
    if enabled and not verify:
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise ValueError("Verification requires a trusted argv")
    # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
    return {
        "schema_version": 1,
        "run_id": uuid4().hex,
        "messages": [SystemMessage(SYSTEM), HumanMessage(task)],
        "task": task,
        "root": str(Path(root).resolve()),
        "plan": "",
        "evidence": {},
        "steps": 0,
        "calls": 0,
        "tokens": 0,
        "usage_missing": 0,
        "max_steps": max_steps,
        "max_calls": max_calls,
        "max_tokens": max_tokens,
        "pending": [],
        "cursor": 0,
        "status": "running",
        "verify_argv": verify,
        "verification_enabled": enabled,
        "baseline": {},
        "verification": {},
        "repairs": 0,
        "max_repairs": max_repairs,
        "feedback": [],
        "error": ""
    }

# 【函数框架 budget】判断已知计数有没有到上限。
# 输入：steps/calls/tokens 与对应上限。 输出：布尔值。
# 1. 检查模型请求上限
# 2. 检查工具尝试上限
# 3. 仅 max_tokens>0 时检查已知 token 小计
def budget(state):
    """token 为响应后的已知累计值；未知用量另记，步数与工具数仍受限。"""
    # 小例子：steps=20、max_steps=20 → True，下一次请求停止。
    # 留意：token 是回复后的软检查，无法事先保证这一请求不超量；未知用量另记。
    return (state["steps"] >= state["max_steps"] or state["calls"] >= state["max_calls"]
            or (state["max_tokens"] > 0 and state["tokens"] >= state["max_tokens"]))

# 【函数框架 usage_update】把一次已有回复的请求计入账本。
# 输入：原 state 和 answer.usage_metadata。 输出：steps、tokens、usage_missing 的新值。
# 1. 读取 total_tokens
# 2. steps 增加 1
# 3. 已知数加入小计，缺失则增加未知请求数
def usage_update(state, answer):
    # 小例子：旧小计 120，本次无 usage → 小计仍 120，usage_missing+1，最终总用量为空。
    # 留意：(total is None) 是布尔值，Python 中 True 可加作 1；未知不是免费。
    usage = answer.usage_metadata or {}
    # 没有 total_tokens 就得到 None；未知用量不能宣称完整总量为 0。
    total = usage.get("total_tokens")
    # 条件表达式：条件 total is not None 成立取前面的值，否则取 else 后面的值。
    # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
    return {"steps": state["steps"] + 1,
            "tokens": state["tokens"] + (total if total is not None else 0),
            "usage_missing": state["usage_missing"] + (total is None)}
