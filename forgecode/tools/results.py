"""结果信封始终是合法 JSON；大内容存档并提供头尾预览。"""
# 【模块框架】结果信封与原文柜：短结果直接送，长结果带编号送预览。
# 连接位置：工具函数 → ToolResult → ResultStore.fit → ToolMessage → read_result
# 流过这里的数据：信封字段 status/content/error_code/call_id/truncated/source_ref/
# duration_ms。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re

# 【类框架 ToolResult】真实结果的数据容器：工具名、请求编号、状态、内容、错误、原文编号与
# 耗时分别保存。
# dataclass 自动生成初始化和字段比较等方法；frozen=True 时限制创建后修改字段。
@dataclass
class ToolResult:
    # name：产生此结果的工具名。
    name: str
    # call_id：关联模型动作单的 ID。
    call_id: str
    # status：ok/error/denied/cancelled/uncertain 等结果状态。
    status: str = "ok"
    # content：实际结果正文，可是文本或结构化对象。
    content: object = ""
    # error_code：失败的机器可读原因；成功通常为 None。
    # 类型中的 | 表示允许其中一种类型；例如 str | None 表示文字或无值，不是在做加法。
    error_code: str | None = None
    # truncated：是否缩小了结果正文预览。
    truncated: bool = False
    # source_ref：长结果的原文编号，交 read_result 回读。
    # 类型中的 | 表示允许其中一种类型；例如 str | None 表示文字或无值，不是在做加法。
    source_ref: str | None = None
    # duration_ms：本次执行耗时，毫秒。
    duration_ms: float = 0

    # 【函数框架 ToolResult.message】把结果对象写成一份合法 JSON 信封。
    # 输入：ToolResult 实例。 输出：JSON 字符串。
    # 1. asdict 展开数据类字段
    # 2. json.dumps 编码
    # 3. ensure_ascii=False 保留可读中文
    def message(self):
        # 小例子：{'status':'ok','content':'2: return a-b',...}。
        # 留意：ToolMessage.content 是字符串，所以要序列化；不要直接截断整个 JSON。
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        # asdict： 把 dataclass 的字段转为普通字典，随后可编码为 JSON。
        return json.dumps(asdict(self), ensure_ascii=False)

    # 【函数框架 ToolResult.error】用统一格式生成失败结果。
    # 输入：工具名、调用 ID、错误代码、错误说明和可选状态。 输出：失败 ToolResult。
    # 1. 把说明转字符串
    # 2. 填入状态与 error_code
    # 3. 返回同一种信封对象
    # @classmethod：自动接收 cls 类对象，常用于构造同类结果。
    @classmethod
    def error(cls, name, call_id, code, text, status="error"):
        # 小例子：审批拒绝 → status='denied', error_code='permission_denied'。
        # 留意：denied、cancelled、uncertain 含义不同，不能全都写成普通文本失败。
        return cls(name, call_id, status, str(text), code)

# 【类框架 ResultStore】长结果管理：短结果直接交回；长结果保存原文并给头尾预览，再通过编号
# 回读。
class ResultStore:
    # 【函数框架 ResultStore.__init__】规定原文柜位置和默认预览大小。
    # 输入：root、limit。 输出：ResultStore 对象。
    # 1. 目录设为 .forgecode/results
    # 2. 记录 limit
    # 3. 只有遇到长结果才实际建目录
    def __init__(self, root, limit=10000):
        # 小例子：默认 content 预览限制 10000 字符。
        # 留意：结果目录属于内部数据，不能用普通 read_file 绕过受控回读。
        self.folder = Path(root) / ".forgecode" / "results"
        self.limit = limit

    # 【函数框架 ResultStore.fit】将长内容存原文，返回带头尾和编号的信封。
    # 输入：result 与可选预览 limit。 输出：可能缩短 content 的同一个结果对象。
    # 1. 把内容转成可计长度的文本
    # 2. 短内容原样返回
    # 3. 长内容按 SHA256 编号写 JSON 原文
    # 4. 保留头尾并设置 truncated/source_ref
    def fit(self, result, limit=None):
        # 小例子：尾部 AssertionError 仍在预览，source_ref 指向原文供分段查。
        # 留意：fit 会修改传入对象；能保存的是执行器已经捕获的内容，进程超过 2MB 被省略的中间段无
        # 法凭空回读。
        # 条件表达式：条件 limit is None 成立取前面的值，否则取 else 后面的值。
        # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
        limit = self.limit if limit is None else limit
        # 条件表达式：条件 isinstance(result.content, str) 成立取前面的值，否则取 else 后面的值。
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        text = result.content if isinstance(result.content, str) else json.dumps(result.content, ensure_ascii=False)
        if len(text) <= limit:
            return result
        # 完整已捕获内容入柜：摘要当编号，JSON 文件保存原对象，后续 read 可还原。
        self.folder.mkdir(parents=True, exist_ok=True)
        # hexdigest： 把摘要转成十六进制文字，便于作为字典值或保存编号。
        # sha256： 把内容算成固定摘要；用来比对版本或参数，不是加密后可以再还原的文字。
        ref = hashlib.sha256(text.encode()).hexdigest()
        # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        (self.folder / (ref + ".json")).write_text(json.dumps(result.content, ensure_ascii=False), encoding="utf-8")
        # 只缩正文，信封和身份保留：头尾各一半，中间注明裁剪和 read_result 方法；source_ref 旧值存
        # 在时保留最早原文编号。
        half = limit // 2
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        result.content = text[:half] + "\n[OUTPUT PRUNED; use read_result with source_ref]\n" + text[-half:]
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        result.truncated, result.source_ref = True, result.source_ref or ref
        return result

    # 【函数框架 ResultStore.read】按结果编号和字符范围借出一段原文。
    # 输入：64 位十六进制 result_id、start、length。 输出：编号、起点、总长度和片段。
    # 1. 验证编号和范围
    # 2. 读取对应 JSON 并还原内容
    # 3. 按字符切片返回
    def read(self, result_id, start=0, length=8000):
        # 小例子：read_result(id,start=8000,length=4000) 读取中间一段。
        # 留意：编号校验防止把 ../secret 当文件路径；这里 start 从 0 开始，与源码行号不同。
        # fullmatch： 整个文字都必须符合指定模式；不能只让其中一小段符合，编号范围见本行模式。
        if not re.fullmatch("[a-f0-9]{64}", result_id):
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Invalid result ID")
        if start < 0 or not 1 <= length <= 10000:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Invalid result range")
        # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
        # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
        payload = json.loads((self.folder / (result_id + ".json")).read_text(encoding="utf-8"))
        # 条件表达式：条件 isinstance(payload, str) 成立取前面的值，否则取 else 后面的值。
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        return {
            "source_ref": result_id,
            "start": start,
            "total_characters": len(text),
            "content": text[start:start+length]
        }
