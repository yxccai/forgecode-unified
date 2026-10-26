"""一次工具执行的唯一路径：校验 → 授权 → 回执 → 函数 → 结果。"""
# 【模块框架】工具柜的总入口：动作必须先过合同、权限和恢复核对，才到真实函数。
# 连接位置：Runtime.tools_node → prepare/execute → Policy/Receipts → 工具函数 → ToolResult
# 流过这里的数据：ToolSpec 把 StructuredTool 与风险级别放一起；specs 用名字查找。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from dataclasses import dataclass
import os
import time
from langchain_core.tools import StructuredTool
from pydantic import ValidationError
from forgecode.context.repository import Repository
from forgecode.execution.cancel import CancelToken, Cancelled
from forgecode.execution.paths import PathGuard
from forgecode.execution.policy import Policy, args_digest
from forgecode.execution.receipts import Receipts, fingerprint
from forgecode.memory import Memory
from forgecode.tools.files import Files
from forgecode.tools.results import ResultStore, ToolResult
from forgecode.tools.adapters import register_capabilities

# 【类框架 ToolSpec】一个登记项，把真实工具对象与风险标签放在一起。模型菜单、参数校验和策
# 略都读这份定义。
# dataclass 自动生成初始化和字段比较等方法；frozen=True 时限制创建后修改字段。
@dataclass
class ToolSpec:
    # tool：StructuredTool：真实函数、说明与参数合同。
    tool: StructuredTool
    # risk：read/write/command/memory，交给策略判断。
    risk: str

# 【类框架 ToolRegistry】工具执行的统一入口：查登记 → 校验参数 → 查回执 → 核对授权 → 调真
# 实函数 → 交结构化结果。
class ToolRegistry:
    # 【函数框架 ToolRegistry.__init__】装配工具柜及其共用服务。
    # 输入：root、功能开关、策略、取消令牌、保护路径与故障注入钩子。 输出：包含工具表和所有能
    # 力对象的 ToolRegistry。
    # 1. 建立路径守卫、检索器和可选记忆
    # 2. 建立文件操作、策略、结果仓库与动作回执
    # 3. 规范化保护路径
    # 4. 初始化 specs，再注册所有开放能力
    def __init__(self, root, repo_context=True, memory=True, store_root=None, policy=None,
                 cancel=None, emit=lambda event: None, protected_paths=(), after_effect=None):
        # 小例子：保护 test_app.py，允许 app.py 的精确修改。
        # 留意：after_effect 是恢复测试用的故障注入位置，不是面向模型的工具。
        self.guard = PathGuard(root)
        self.root = self.guard.root
        self.repository = Repository(self.root)
        self.repo_context = repo_context
        # 条件表达式：条件 memory 成立取前面的值，否则取 else 后面的值。
        self.memory = Memory(self.root, store_root, emit) if memory else None
        self.files = Files(self.guard, self.repository)
        self.policy = policy or Policy()
        self.cancel = cancel or CancelToken()
        self.results = ResultStore(self.root)
        self.receipts = Receipts(self.root)
        # 推导式把循环写紧凑：从 protected_paths 逐项取出 path，收集成集合。
        self.protected_paths = {self.path_key(path) for path in protected_paths}
        self.after_effect = after_effect
        self.specs = {}
        register_capabilities(self)

    # 【函数框架 ToolRegistry.items】把内部工具表变成模型能读的菜单。
    # 输入：specs 中的 ToolSpec。 输出：StructuredTool 列表。
    # 1. 遍历登记项
    # 2. 提取每项的 tool
    # 3. 返回给模型绑定菜单
    # @property：调用者像读字段一样使用它，读取时仍会执行这个方法。
    @property
    def items(self):
        # 小例子：菜单显示 edit_file(path,old,new)，执行仍检查 Policy。
        # 留意：风险信息仍留在柜内，模型看到菜单不能越过真实执行检查。
        # 推导式把循环写紧凑：从 self.specs.values() 逐项取出 spec，收集成列表。
        return [spec.tool for spec in self.specs.values()]

    # 【函数框架 ToolRegistry.add】登记一个工具的合同和风险。
    # 输入：name、真实 function、参数 schema、risk、说明。 输出：写入 specs；无值返回。
    # 1. 拒绝重名与未知风险
    # 2. 用 StructuredTool 打包函数和参数模型
    # 3. 按名字登记 ToolSpec
    def add(self, name, function, schema, risk, description=None):
        # 小例子：登记 read_file 为 read，edit_file 为 write，run_command 为 command。
        # 留意：说明必须让模型知道什么时候使用；参数合同也必须在执行时校验。
        if name in self.specs:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Duplicate tool")
        if risk not in {"read", "write", "command", "memory"}:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Unknown tool risk category")
        # from_function 把真实函数、工具名字、使用说明、参数 schema 打包。这里只传 function 这个可
        # 调用对象，没有写 function()，所以此刻不读文件、不改代码。args_schema 用于后面的参数检查；
        # risk 留在本地策略层。
        tool = StructuredTool.from_function(function, name=name, description=description or function.__doc__,
                                            args_schema=schema)
        self.specs[name] = ToolSpec(tool, risk)

    # 【函数框架 ToolRegistry.path_key】把保护路径转换成稳定的比较键。
    # 输入：用户/工具提供的 path。 输出：规范化相对路径字符串。
    # 1. 先经过 PathGuard
    # 2. 相对化并统一斜杠
    # 3. Windows 再 casefold 消除大小写差异
    def path_key(self, path):
        # 小例子：保护 'test_app.py' 后，大写形式也会被拦。
        # 留意：./test_app.py 与 TEST_APP.PY 在 Windows 不能借写法差异绕过保护。
        # as_posix： 把路径表示成使用 / 的文字，便于跨模块保存和比对。
        # relative_to： 取相对于指定根目录的路径；不在该根下会报错，不能用字符串前缀冒充这个检查。
        value = self.guard.path(path).relative_to(self.root).as_posix()
        # 条件表达式：条件 os.name == 'nt' 成立取前面的值，否则取 else 后面的值。
        # casefold： 用较彻底的大小写归一化方式比较文字；本项目在 Windows 路径比较处使用。
        return value.casefold() if os.name == "nt" else value

    # 【函数框架 ToolRegistry.prepare】在执行前把动作单改成经过验证的规范参数。
    # 输入：工具名字与 args 字典。 输出：spec 与 normalized 参数；错误则抛异常。
    # 1. 查工具是否登记
    # 2. schema.model_validate 校验再 model_dump
    # 3. 写工具检查是否指向受保护文件
    def prepare(self, name, args):
        # 小例子：read_file(start=0) 会失败，不能悄悄当作第 1 行。
        # 留意：校验在副作用之前；不认识的参数由 extra='forbid' 拒绝。
        if name not in self.specs:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise KeyError("Unknown tool: " + name)
        # 按名字查合同：name 对应登记项 spec。它告诉程序应该用哪种参数类、什么风险和真实函数。
        spec = self.specs[name]
        # 检查后转成普通字典：model_validate 用参数合同检查本张请求，model_dump 取出通过检查的字段。
        # 没通过就走错误返回，尚未执行工具。
        normalized = spec.tool.args_schema.model_validate(args).model_dump()
        if spec.risk == "write" and "path" in normalized and self.path_key(normalized["path"]) in self.protected_paths:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise PermissionError("Verification file is protected")
        return spec, normalized

    # 【函数框架 ToolRegistry.execute】唯一的真实工具通道：先核对，再授权，再动手，最后记录。
    # 输入：name、args、call_id、可选 Grant 与 operation_id。 输出：统一 ToolResult，失败也有
    # 结构化状态。
    # 1. 准备参数并核对旧动作回执
    # 2. 已有完成结果直接返回；编辑已达预期则补回执
    # 3. 效果未知则 uncertain，尚未执行才核对当前授权
    # 4. 副作用前记 begin，再调用工具
    # 5. 异常转换为结果，保存长输出并完成回执
    def execute(self, name, args, *, call_id="", grant=None, operation_id=None):
        """恢复时完成回执直接回放；未知命令结果返回 uncertain，等待核对。"""
        # 小例子：文件已改成 a+b 但还没 checkpoint 就崩溃：重开核对 after 指纹，补回执，不再替换一
        # 次。
        # 留意：通用命令无法仅凭文件指纹证明完成；恢复时不盲目重跑。这里只覆盖单执行所有者。
        # 计时并校验合同：prepare 将参数规范化并检查工具存在。只有合法参数才有资格进入恢复核对或执
        # 行。
        started = time.perf_counter()
        try:
            # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
            spec, args = self.prepare(name, args)
            # 条件表达式：条件 operation_id 成立取前面的值，否则取 else 后面的值。
            saved = self.receipts.get(operation_id) if operation_id else None
            # 先核对稳定编号下的记录是否属于这次同名、同参数动作；不匹配说明记录不能复用。
            if saved:
                expected = saved["expected"]
                if expected.get("name") != name or expected.get("args_digest") != args_digest(args):
                    # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                    raise ValueError("Operation receipt does not match this action")
                # 做过的动作先查回单：已有完成结果就还原 ToolResult 并返回；不会因为旧进度没更新就再次执行。
                if saved["result"]:
                    # 调用时的 ** 把字典按参数名展开；字段 key 的值相当于用 key=值 传给函数。
                    return ToolResult(**saved["result"])
                # 核对磁盘上的实际编辑效果：无完成回单但文件已是预期 after 指纹，可以补回单。无法确认的通
                # 用命令返回 uncertain 等待检查。
                if name == "edit_file" and fingerprint(self.guard.path(args["path"])) == expected["after"]:
                    result = ToolResult(name, call_id, content={"edited": args["path"], "recovered": True})
                    # 把已获得的真实结果写进完成回执；这与图保存进度是两个不同的保存动作。
                    self.receipts.finish(operation_id, result)
                    return result
                if name != "edit_file" or fingerprint(self.guard.path(args["path"])) != expected.get("before"):
                    return ToolResult.error(name, call_id, "uncertain_execution", "Previous action started; its effect is unknown. Inspect before retrying.", "uncertain")
            # 批准不匹配，先返回：在真实函数调用前检查策略与本次批准。没通过就返回 denied，不继续写文
            # 件或启动命令。
            if not self.policy.permitted(self.root, name, args, call_id, spec.risk, grant):
                return ToolResult.error(name, call_id, "permission_denied", "Action denied by execution policy", "denied")
            self.cancel.check()
            # 先写开始单，再调用真实函数：非读取动作先 begin。编辑的预期来自 prepare_edit。invoke 是实
            # 际副作用发生的位置；副作用之后可能故障注入，模拟尚未 checkpoint 就断电。
            if operation_id and spec.risk != "read" and not saved:
                expected = {"name": name, "args_digest": args_digest(args)}
                if name == "edit_file":
                    # 推导式把循环写紧凑：从 self.files.prepare_edit(**args).items() 逐项取出 (k, v)，仅保留满
                    # 足 k != 'replacement' 的项，收集成字典。
                    # 调用时的 ** 把字典按参数名展开；字段 key 的值相当于用 key=值 传给函数。
                    expected.update({k: v for k, v in self.files.prepare_edit(**args).items() if k != "replacement"})
                # 真正动手之前先存预期；这样中断重开后仍知道应该核对什么。
                self.receipts.begin(operation_id, expected)
            # 到这里才真正动手：登记的真实函数接到已校验参数，value 接住实际结果。之后封装回单，不让口
            # 头承诺代替事实。
            value = spec.tool.invoke(args)
            result = ToolResult(name, call_id, content=value)
            if isinstance(value, dict) and value.get("cancelled"):
                # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
                result.status, result.error_code = "cancelled", "cancelled"
            elif isinstance(value, dict) and value.get("timed_out"):
                # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
                result.status, result.error_code = "error", "timeout"
            elif name == "run_command" and value.get("exit_code") != 0:
                # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
                result.status, result.error_code = "error", "command_failed"
            if self.after_effect and spec.risk != "read":
                self.after_effect(name, args)
        # 把不同失败变成统一信封：schema 错、未知工具、权限拒绝、取消、文件异常分别有 error_code。
        # 捕捉范围不是随便吞掉所有异常：恢复测试的特定断电故障会向上冒出。
        except ValidationError as exc:
            result = ToolResult.error(name, call_id, "invalid_args", exc)
        except KeyError as exc:
            result = ToolResult.error(name, call_id, "unknown_tool", exc)
        except PermissionError as exc:
            result = ToolResult.error(name, call_id, "permission_denied", exc, "denied")
        except Cancelled as exc:
            result = ToolResult.error(name, call_id, "cancelled", exc, "cancelled")
        except (ValueError, TypeError, OSError) as exc:
            result = ToolResult.error(name, call_id, "execution_error", exc)
        # 收报告并盖完成章：添加时长，长输出 fit 保存原文，再 finish 回执。回执与 checkpoint 分开，
        # 正是需要前面现实核对的原因。
        result.duration_ms = round((time.perf_counter()-started)*1000, 2)
        result = self.results.fit(result)
        if operation_id and self.receipts.get(operation_id):
            self.receipts.finish(operation_id, result)
        return result

    # 【函数框架 ToolRegistry.git_diff】提供统一的差异查看入口。
    # 输入：当前工具柜。 输出：Files.git_diff 的真实结果。
    # 1. 委托文件能力
    # 2. 读取当前仓库差异
    # 3. 返回给收尾或模型
    def git_diff(self):
        # 小例子：Runtime.finish 用它展示 app.py 的实际变化。
        # 留意：它只转发，没有另写一套 Git 差异实现。
        return self.files.git_diff()
