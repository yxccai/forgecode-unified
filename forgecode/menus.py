"""Interactive settings only. No dependency on agent state, tools or prompts."""
# 【模块框架】设置菜单：选择模型、推理档位、API 设置并保存。
# 连接位置：交互 /api /model /effort → 菜单 → Config
# 流过这里的数据：所有菜单只修改配置或本地配置文件；不接触 Runtime 状态。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from dataclasses import asdict
from getpass import getpass
import json
import os
from pathlib import Path
import urllib.request


# 【函数框架 choose】显示列表，接受序号、直接输入或取消。
# 输入：标题、选项、当前值、是否允许自定义。 输出：选项字符串或原值。
# 1. 打印序号和当前标记
# 2. 读取输入
# 3. 空/0 保留当前，合法数字选项，允许时接自定义文本
# 4. 非法输入报错
def choose(console, title, options, current="", custom=True):
    # 小例子：选 1 用第一项；输入模型 ID 可手动设定。
    # 留意：数字超范围不能当自定义值混入。
    console.print(title, style="bold cyan", markup=False)
    for index, option in enumerate(options, 1):
        # 条件表达式：条件 option == current 成立取前面的值，否则取 else 后面的值。
        console.print(f"  {index}. {option}" + (" [current]" if option == current else ""), markup=False)
    console.print("  0. Cancel", style="dim")
    # 条件表达式：条件 custom 成立取前面的值，否则取 else 后面的值。
    value = input("Number or value: " if custom else "Number: ").strip()
    if not value or value == "0":
        return current
    if value.isdigit() and 1 <= int(value) <= len(options):
        return options[int(value) - 1]
    if custom and not value.isdigit():
        return value
    # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
    raise ValueError("Invalid selection")


# 【函数框架 pick_model】尽量取服务商模型列表，失败仍允许手工输入。
# 输入：config、console。 输出：选中 model ID。
# 1. 把当前模型作为候选
# 2. 有密钥时 GET /models，限制读取量和超时
# 3. 异常显示列表不可用
# 4. 调用 choose
def pick_model(config, console):
    # 小例子：端点不提供 /models 时，直接输入已知模型 ID。
    # 留意：菜单中的 GET 会访问配置端点；离线教材不调用它，也不把模型列表抄成固定推荐。
    # 条件表达式：条件 config.model 成立取前面的值，否则取 else 后面的值。
    models = [config.model] if config.model else []
    if config.api_key:
        try:
            request = urllib.request.Request(config.base_url.rstrip("/") + "/models",
                headers={"Authorization": "Bearer " + config.api_key})
            # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
            with console.status("Loading provider models..."):
                # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
                with urllib.request.urlopen(request, timeout=10) as response:
                    # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
                    payload = json.loads(response.read(1_000_000))
            # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
            # 推导式把循环写紧凑：从 payload.get('data', []) 逐项取出 item，仅保留满足 isinstance(
            # item.get('id'), str) 的项，收集成列表。
            models = sorted(set(models + [item["id"] for item in payload.get("data", [])
                                          if isinstance(item.get("id"), str)]))[:100]
        except Exception:
            console.print("Model list unavailable. Enter a model ID directly.", style="yellow")
    return choose(console, "Model", models, config.model)


# 【函数框架 pick_effort】选择服务商可能支持的推理档位。
# 输入：config、console。 输出：档位字符串，default 为 None。
# 1. 显示预设档位
# 2. 调用 choose
# 3. default 转 None 表示不传该字段
def pick_effort(config, console):
    # 小例子：default 让请求省略 reasoning_effort。
    # 留意：列表不保证所有端点都支持，配置必须符合服务商能力。
    value = choose(console, "Reasoning effort (supported values depend on provider)",
                   ["default", "none", "minimal", "low", "medium", "high", "xhigh"],
                   config.reasoning_effort or "default")
    # 条件表达式：条件 value == 'default' 成立取前面的值，否则取 else 后面的值。
    return None if value == "default" else value


# 【函数框架 save】把当前数据类配置保存为私有本地 JSON。
# 输入：Config、path。 输出：配置文件。
# 1. 创建目录
# 2. 以限制模式打开
# 3. asdict + json.dump 写入
# 4. 再次设置权限
def save(config, path):
    # 小例子：/save 写 .forgecode/config.local.json。
    # 留意：配置含密钥，需保持在本地忽略目录；Windows ACL 不完全等价于 0o600。
    target = Path(path)
    # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
    with os.fdopen(descriptor, "w") as stream:
        # asdict： 把 dataclass 的字段转为普通字典，随后可编码为 JSON。
        json.dump(asdict(config), stream, indent=2)
        stream.write("\n")
    target.chmod(0o600)


# 【函数框架 api_menu】运行 API 设置小循环。
# 输入：config、console、保存 path。 输出：修改配置，按选择保存或返回。
# 1. choose 选择要改字段
# 2. URL 校验协议，密钥用 getpass
# 3. 模型/档位调用对应菜单
# 4. 保存并返回，或不保存返回
def api_menu(config, console, path):
    # 小例子：改 URL 后选择 Save and return 才保存下次启动的配置。
    # 留意：Return without saving 只是没写磁盘，内存中修改已发生。
    while True:
        action = choose(console, "API settings", ["Base URL", "API key", "Model", "Reasoning effort",
                                                    "Save and return", "Return without saving"], custom=False)
        if not action or action == "Return without saving":
            return
        if action == "Base URL":
            url = input("Base URL: ").strip()
            if url:
                if not url.startswith(("http://", "https://")):
                    # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                    raise ValueError("Base URL must start with http:// or https://")
                config.base_url = url
        elif action == "API key":
            value = getpass("API key (empty keeps current): ")
            if value:
                config.api_key = value
        elif action == "Model":
            config.model = pick_model(config, console)
        elif action == "Reasoning effort":
            config.reasoning_effort = pick_effort(config, console)
        elif action == "Save and return":
            save(config, path)
            console.print(f"Saved: {path}", markup=False)
            return
