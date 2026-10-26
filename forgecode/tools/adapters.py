"""把检索、记忆、命令接到同一工具表；能力对象无需继承工具表。"""
# 【模块框架】工具合同册：字段类型、默认值和风险在这里公开登记。
# 连接位置：ToolRegistry.__init__ → register_capabilities → add → 模型菜单
# 流过这里的数据：Args 禁止额外字段；ReadArgs 等类只声明字段；Field 设置边界，不需要手写方
# 法。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from pydantic import BaseModel, ConfigDict, Field
from langchain_core.tools import StructuredTool
from forgecode.execution.process import command

# 【类框架 Args】所有工具参数合同的基础类。extra="forbid" 拒绝合同之外的字段，避免拼错参数
# 仍被接受。
class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")

# 【类框架 ReadArgs】read_file 的参数合同：path 指文件，start 指开始行。这里只声明形状，还
# 没有读取文件。
class ReadArgs(Args):
    # path：围栏内相对文件名；不允许绝对路径和内部数据。
    path: str
    # start：源码起始行号，最小1；一次最多200行。
    start: int = Field(default=1, ge=1)

# 【类框架 SearchArgs】检索参数合同：query 至少一个字符。搜索实际由登记的函数执行。
class SearchArgs(Args):
    # query：非空查询文本；具体能力可做字面搜索或目录检索。
    query: str = Field(min_length=1)

# 【类框架 EditArgs】edit_file 的参数合同：path、old、new 三项都是文字；是否唯一匹配由文件
# 工具另行核对。
class EditArgs(Args):
    # path：要改的相对路径；保护验证文件另有检查。
    # 写清三个必需输入：path 是文件名，old 是原片段，new 是替换片段。冒号后的 str 说明预期是文
    # 字，尚未给字段填值或修改文件。
    path: str
    # old：必须在已有文件唯一出现的原片段；创建文件时要求空。
    old: str
    # new：要换进去的新片段；真实 prepare_edit 限制大小。
    new: str

# 【类框架 CommandArgs】命令参数合同：argv 是非空文字列表。执行器按参数列表启动，不解释
# shell 拼接。
class CommandArgs(Args):
    # argv：至少一个字符串参数，执行器不解释 shell 拼接。
    argv: list[str] = Field(min_length=1)

# 【类框架 RememberArgs】写笔记的参数合同：内容、来源列表、种类。来源仍有效与结论经验证是
# 两回事。
class RememberArgs(Args):
    # note：来源关联的笔记文本，不等于已经验证结论。
    note: str
    # sources：该笔记引用的相对文件路径列表。
    sources: list[str]
    # kind：semantic 事实笔记或 episodic 任务经历。
    kind: str = "semantic"

# 【类框架 ResultArgs】回读长结果的参数合同：编号、字符起点、长度。它不是任意文件读取入口。
class ResultArgs(Args):
    # result_id：64位十六进制原文编号，不是任意文件路径。
    result_id: str
    # start：从0开始的字符下标，不是源码行号。
    start: int = Field(default=0, ge=0)
    # length：读取字符数，最小1、最大10000。
    length: int = Field(default=8000, ge=1, le=10000)

# 【类框架 EmptyArgs】不接受业务参数的合同。pass 表示类体无需添加字段；基础类仍拒绝额外输
# 入。
class EmptyArgs(Args):
    pass

# 【函数框架 register_capabilities】把现有能力登记为统一菜单。
# 输入：registry。 输出：更新 registry.specs。
# 1. 注册文件读、查、改与 diff
# 2. 定义命令包装函数并登记 command
# 3. 按开关登记检索和记忆
# 4. 总是登记长结果回读
def register_capabilities(registry):
    """新增一个工具只需注册函数、schema、说明和风险类别。"""
    # 小例子：memory=False 时菜单没有 remember/recall_memory，其余核心循环不改。
    # 留意：能力对象不必继承工具表；新增本地工具只登记函数、schema、风险和说明。
    registry.add("read_file", registry.files.read_file, ReadArgs, "read")
    registry.add("search", registry.files.search, SearchArgs, "read")
    registry.add("list_files", registry.files.list_files, EmptyArgs, "read")
    # 把名字、函数、合同、风险放进登记表：edit_file 是模型看到的名字；后面依次是真实方法、
    # EditArgs 参数合同和 write 风险标签。没有调用括号，所以这里仅登记。
    registry.add("edit_file", registry.files.edit_file, EditArgs, "write")
    registry.add("git_diff", registry.files.git_diff, EmptyArgs, "read")

    # 【函数框架 register_capabilities.run_command】把模型动作接到共用的命令执行器。
    # 输入：argv 字符串列表。 输出：真实退出码、输出、超时/取消信息。
    # 1. 使用外层 registry 的工作根
    # 2. 传入共用取消令牌
    # 3. 调用 command
    def run_command(argv: list[str]):
        """Run an approved argv command without shell syntax."""
        # 小例子：argv=['python','-m','unittest','discover']，不写一整串 shell 命令。
        # 留意：函数内部不问审批，审批已经由工具柜统一处理；shell=False 不代表命令本身没有风险。
        return command(registry.root, argv, cancel=registry.cancel)
    registry.add("run_command", run_command, CommandArgs, "command")

    if registry.repo_context:
        # 【函数框架 register_capabilities.repo_map】把模型给的查询接到仓库检索。
        # 输入：query 文本。 输出：Repository.select 的目录证据。
        # 1. 使用闭包中的 registry.repository
        # 2. 把 query 作为 task 传入
        # 3. 返回选中位置
        def repo_map(query: str):
            # 小例子：repo_map('add app.py') → add 的路径和行号。
            # 留意：找候选不等于读原文；模型还应调用 read_file 确认。
            return registry.repository.select(query)
        registry.add("repo_map", repo_map, SearchArgs, "read",
                     description="Find task-relevant paths and Python symbols. Query is ordinary text.")
        registry.add("find_symbols", registry.repository.symbols, SearchArgs, "read",
                     description="Locate Python function/class definitions.")
    if registry.memory:
        registry.add("recall_memory", registry.memory.recall, SearchArgs, "read",
                     description="Recall relevant source-checked repository notes.")
        registry.add("remember", registry.memory.remember, RememberArgs, "memory",
                     description="Store a source-linked note; this does not prove the note true.")
    registry.add("read_result", registry.results.read, ResultArgs, "read",
                 description="Read a captured long tool result by its source_ref ID and character range.")
