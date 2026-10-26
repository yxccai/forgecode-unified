"""Optional WSL bridge. Credentials stay in captured output and process memory."""
# 【模块框架】可选 Windows 设置桥：从系统变量取配置到当前进程。
# 连接位置：load_config(windows=True) → windows_environment → 当前 os.environ
# 流过这里的数据：只读 FORGECODE_API_KEY/BASE_URL/MODEL；捕获输出，不把密钥打印。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import json
import os
import subprocess


# 【函数框架 windows_environment】读取 Windows 三个配置变量，并安装到当前 Python 进程。
# 输入：Windows 用户/机器/进程环境变量。 输出：当前 os.environ 增加三项，缺项则报错。
# 1. PowerShell 按 User→Machine→Process 优先查
# 2. 捕获 JSON，不回显
# 3. 核对三项都是非空字符串
# 4. 写当前进程环境
def windows_environment():
    # 小例子：--windows-env 显式要求桥，缺配置时明确失败。
    # 留意：不会全局设置或覆盖用户系统变量；当前开发原生 Windows 也可不使用这座桥。
    script = """
    $result = @{}
    foreach ($name in @('FORGECODE_API_KEY', 'FORGECODE_BASE_URL', 'FORGECODE_MODEL')) {
        $value = [Environment]::GetEnvironmentVariable($name, 'User')
        if (-not $value) { $value = [Environment]::GetEnvironmentVariable($name, 'Machine') }
        if (-not $value) { $value = [Environment]::GetEnvironmentVariable($name, 'Process') }
        $result[$name] = $value
    }
    $result | ConvertTo-Json -Compress
    """
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=30)
    # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
    values = json.loads(result.stdout.decode("utf-8-sig"))
    names = ("FORGECODE_API_KEY", "FORGECODE_BASE_URL", "FORGECODE_MODEL")
    # 推导式把循环写紧凑：从 names 逐项取出 name，收集成迭代器。
    if not all(isinstance(values.get(name), str) and values[name] for name in names):
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise RuntimeError("Missing Windows FORGECODE variables")
    for name in names:
        os.environ[name] = values[name]
