# 【模块框架】电话线设置表：端点、模型、超时和能力开关。
# 连接位置：入口 → Config.load/configure → Model + ContextBuilder
# 流过这里的数据：Config 是数据类；默认值加 JSON/环境/显式覆盖，密钥保存在本地私有配置。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import json
import os
from dataclasses import dataclass
from pathlib import Path


# 【类框架 Config】连接与预算配置的数据容器。字段保存设置，load/save 负责读写，配置本身不
# 执行任务。
# dataclass 自动生成初始化和字段比较等方法；frozen=True 时限制创建后修改字段。
@dataclass
class Config:
    # base_url：模型 API 的基础地址；发送给哪个服务。
    base_url: str = "https://api.openai.com/v1"
    # api_key：访问端点用的本地密钥；日志需脱敏。
    api_key: str = ""
    # model：端点接受的具体模型 ID，加载时不能为空。
    model: str = ""
    # reasoning_effort：可选推理档位；None 表示请求不传该字段，取值由端点支持。
    # 类型中的 | 表示允许其中一种类型；例如 str | None 表示文字或无值，不是在做加法。
    reasoning_effort: str | None = None
    # max_steps：交互/任务使用的逻辑模型请求上限。
    max_steps: int = 20
    # timeout：单模型请求超时秒数，不同于工具 command 默认30秒。
    timeout: float = 90
    # streaming：True 逐片收；False 一次拿完整回复。
    streaming: bool = True
    # supports_tools：端点能否接工具菜单；否则有 tools 时明确拒绝。
    supports_tools: bool = True
    # supports_usage：端点是否支持用量报告请求；缺少实际 usage 仍需记未知。
    supports_usage: bool = True
    # context_char_limit：本次输入的近似字符容量，不是精确 token。
    context_char_limit: int = 32000

    # 【函数框架 Config.load】按清楚的优先级合并配置并校验。
    # 输入：可选 JSON path 与 **overrides。 输出：Config 对象。
    # 1. 读取 JSON
    # 2. FORGECODE/OPENAI 环境值覆盖文件
    # 3. 非 None 的显式 overrides 再覆盖
    # 4. 实例化并校验模型、步数、超时、上下文容量
    # @classmethod：自动接收 cls 类对象，常用于构造同类结果。
    @classmethod
    def load(cls, path=None, **overrides):
        # 通用入口：显式 overrides > 环境 > JSON；交互入口会把选定 JSON 再作为 overrides。
        # 小例子：文件 model=A、环境 model=B、--model=C → 使用 C。
        # 留意：通用优先级是显式覆盖>环境>JSON；交互选定配置会再次作为显式覆盖传入。
        # 条件表达式：条件 path 成立取前面的值，否则取 else 后面的值。
        # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
        # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
        values = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
        for key, suffix in (("base_url", "BASE_URL"), ("api_key", "API_KEY"),
                            ("model", "MODEL"), ("reasoning_effort", "REASONING_EFFORT")):
            value = os.getenv("FORGECODE_" + suffix) or os.getenv("OPENAI_" + suffix)
            if value:
                values[key] = value
        # 推导式把循环写紧凑：从 overrides.items() 逐项取出 (key, value)，仅保留满足 value is not
        # None 的项，收集成字典。
        # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
        values.update({key: value for key, value in overrides.items() if value is not None})
        # 调用时的 ** 把字典按参数名展开；字段 key 的值相当于用 key=值 传给函数。
        result = cls(**values)
        if not result.model:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Set FORGECODE_MODEL, OPENAI_MODEL or --model")
        if result.max_steps < 1 or result.timeout <= 0 or result.context_char_limit < 1000:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("max_steps and timeout must be positive; context_char_limit must be >= 1000")
        return result


# 【函数框架 configure】交互填写配置，密钥输入不回显。
# 输入：目标 path 与用户输入。 输出：本地配置文件。
# 1. 载入已有值，空输入保留
# 2. getpass 读取密钥
# 3. 建父目录，以 0o600 写 JSON 并提示路径
def configure(path="config.local.json"):
    """Keep secrets out of command arguments and create a private local config."""
    # 小例子：configure .forgecode/config.local.json 后用 --config 指向它。
    # 留意：配置不是教材的一部分，不把真实密钥粘进命令或示例；Windows 权限机制与 POSIX 模式不
    # 同。
    from getpass import getpass
    target = Path(path)
    # 条件表达式：条件 target.exists() 成立取前面的值，否则取 else 后面的值。
    # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
    # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
    values = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    for key, prompt in (("base_url", "Base URL"), ("model", "Model"),
                        ("reasoning_effort", "Reasoning effort (provider-specific)")):
        value = input(f"{prompt} [{values.get(key) or ''}]: ").strip()
        if value:
            values[key] = value
    secret = getpass("API key (empty keeps existing): ")
    if secret:
        values["api_key"] = secret
    # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(values, stream, indent=2)
        stream.write("\n")
    target.chmod(0o600)
    print(f"Saved {target}. Use --config {target}")
