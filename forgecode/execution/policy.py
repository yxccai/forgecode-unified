"""授权绑定到这一次工具、参数、调用 ID 与工作区。"""
# 【模块框架】门卫与单次通行证：动作是否能做，必须用当前策略判断。
# 连接位置：tools_node 请求审批 → Policy.request/grant → ToolRegistry.execute → permitted
# 流过这里的数据：Grant 是不可变数据类，绑定 root/name/call_id/args_digest/allowed。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

# 【函数框架 args_digest】给规范参数生成稳定指纹。
# 输入：args 字典。 输出：SHA256 字符串。
# 1. 按键排序 JSON 编码
# 2. UTF-8 转字节
# 3. 计算摘要
def args_digest(args):
    # 小例子：{'path':'app.py','new':'a+b'} 换键顺序仍对应同一摘要。
    # 留意：排序让键的书写顺序不影响指纹；摘要不是密钥或加密。
    # hexdigest： 把摘要转成十六进制文字，便于作为字典值或保存编号。
    # sha256： 把内容算成固定摘要；用来比对版本或参数，不是加密后可以再还原的文字。
    # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

# 【类框架 Grant】一张具体动作的批准记录：目录、工具、单号、参数摘要、允许标记全部绑定。
# dataclass 自动生成初始化和字段比较等方法；frozen=True 时限制创建后修改字段。
@dataclass(frozen=True)
class Grant:
    # root：该次授权绑定的工作目录。
    # 批准要绑定具体对象：root、name、call_id、digest、allowed 分别记录目录、工具名、单号、参
    # 数指纹和批准结果。数据类自己不执行命令。
    root: str
    # name：该次授权绑定的工具名。
    name: str
    # call_id：只批准这一张动作单，不扩大到以后调用。
    call_id: str
    # digest：规范参数的稳定摘要；参数变了通行证就不匹配。
    digest: str
    # allowed：只有用户明确 True 才是允许。
    allowed: bool

# 【类框架 Policy】执行策略：先决定 allow/ask/deny，再核对需要的批准是否与当前动作完全匹配。
# dataclass 自动生成初始化和字段比较等方法；frozen=True 时限制创建后修改字段。
@dataclass(frozen=True)
class Policy:
    # mode：task/interactive/readonly 的门禁规则。
    mode: str = "task"

    # 【函数框架 Policy.__post_init__】数据类建好后验证运行模式。
    # 输入：Policy.mode。 输出：合法 Policy，或抛 ValueError。
    # 1. dataclass 自动填入 mode
    # 2. 核对 task/interactive/readonly
    # 3. 非法值拒绝
    def __post_init__(self):
        # 小例子：Policy('readonly') 可用；Policy('anything') 拒绝。
        # 留意：__post_init__ 是数据类构造后的钩子，不是每个工具前重新触发。
        if self.mode not in {"task", "interactive", "readonly"}:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Unknown execution mode")

    # 【函数框架 Policy.decision】按模式和动作风险返回初步决定。
    # 输入：risk 与当前 mode。 输出：allow、ask 或 deny。
    # 1. 只读模式拒绝非 read
    # 2. 命令总要 ask；交互写文件也 ask
    # 3. 其余允许
    def decision(self, risk):
        # 小例子：interactive + write → ask；readonly + command → deny。
        # 留意：任务模式是在专用 worktree 中允许写文件；memory 风险在交互模式当前默认允许，不能宣
        # 称所有副作用均询问。
        # 只读模式先拒绝写操作：risk 是工具登记的风险类别。只读模式下，非 read 动作直接 deny。
        if self.mode == "readonly" and risk != "read":
            return "deny"
        # 这些情况需要本次批准：命令需要 ask；interactive 模式下的写动作也需要 ask。其余走最后的
        # allow。
        if risk == "command" or (risk == "write" and self.mode == "interactive"):
            return "ask"
        return "allow"

    # 【函数框架 Policy.request】构造能让人核对的一次审批单。
    # 输入：root、name、规范 args、call_id、risk。 输出：包含 cwd、参数指纹和调用身份的字典。
    # 1. 把命令/编辑分类展示
    # 2. 规范化 root 并计算参数摘要
    # 3. 附上原参数以便阅读
    def request(self, root, name, args, call_id, risk):
        # 小例子：显示 edit_file、app.py、old=a*b、new=a+b。
        # 留意：审批对象是这一张具体单，不是给所有未来动作开绿灯。
        # 条件表达式：条件 risk == 'command' 成立取前面的值，否则取 else 后面的值。
        # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
        request = {"kind": "command" if risk == "command" else "edit",
                   "name": name, "args": args, "cwd": str(Path(root).resolve()),
                   "tool_call_id": call_id, "args_digest": args_digest(args)}
        request.update(args)
        return request

    # 【函数框架 Policy.grant】把审批回答封成不可变通行证。
    # 输入：request 与 approved。 输出：Grant。
    # 1. 复制审批单的绑定字段
    # 2. 只有 approved is True 才允许
    # 3. 返回 frozen 数据类
    def grant(self, request, approved):
        # 小例子：用户确认后通行证包含该 call_id 和参数 digest。
        # 留意：字符串 'yes' 或数字 1 不会被随意当作明确批准。
        # is True 要求明确的布尔真；在批准判断中，1 或非空文字不能代替明确批准。
        return Grant(request["cwd"], request["name"], request["tool_call_id"],
                     request["args_digest"], approved is True)

    # 【函数框架 Policy.permitted】执行前最后核验这张通行证是否匹配。
    # 输入：当前 root/name/args/call_id/risk 与 grant。 输出：布尔值。
    # 1. allow 直接允许，deny 拒绝
    # 2. ask 没通行证就拒绝
    # 3. 重建当前动作的预期 Grant 并做全字段相等比较
    def permitted(self, root, name, args, call_id, risk, grant):
        # 小例子：批准 app.py，执行参数变 test_app.py → False。
        # 留意：改了参数、目录或调用 ID 就失效；不可用审批前那份参数指纹替代当前核验。
        # 先看策略结果：直接允许就 True；拒绝或缺少所需批准就 False。需要批准的路径还要比较整张
        # Grant。
        decision = self.decision(risk)
        if decision == "allow":
            return True
        # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
        if decision == "deny" or grant is None:
            return False
        # 这张批准单是否完全匹配：按目录、工具、单号、参数摘要和明确 True 一起比较。改了参数就需要
        # 新的匹配批准。
        return grant == Grant(str(Path(root).resolve()), name, call_id, args_digest(args), True)
