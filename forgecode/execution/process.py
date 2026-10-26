"""真实命令执行：无 shell、受控环境、超时/取消与进程清理。"""
# 【模块框架】命令工位：执行 argv、等结果、超时或取消时清理进程。
# 连接位置：run_command/Verification/Git diff → command → 子进程 → 真实结果
# 流过这里的数据：返回 exit_code/timed_out/cancelled/output/truncated/cleanup/duration_ms。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import os
import signal
import subprocess
import sys
import tempfile
import time
from forgecode.execution.cancel import CancelToken

# 【函数框架 command】受控执行一条已由调用方授权的命令。
# 输入：root、argv 列表、timeout、CancelToken。 输出：真实进程结果字典。
# 1. 检查参数，Python 别名换当前解释器
# 2. 过滤常见密钥环境变量，输出写临时文件
# 3. 启动无 shell 进程并尽量绑定进程组/Windows Job
# 4. 短间隔等待，观察超时和取消
# 5. finally 清理并等待退出
# 6. 按 2MB 上限捕获头尾输出，返回真实退出码
def command(root, argv, timeout=30, cancel=None):
    """调用者先授权；返回真实退出码。Python 别名使用当前解释器。"""
    # 小例子：['python','-m','unittest','discover']；exit_code=1 是真实测试失败。
    # 留意：无 shell 只是不解释 && 等语法，Python 或其他可执行程序仍有其自身能力；不能叫系统沙
    # 箱。
    # 先让参数明确：argv 必须是字符串列表且无 NUL。python/python3 换成当前解释器，让样例测试使
    # 用这次独立环境。
    # 推导式把循环写紧凑：从 argv 逐项取出 a，收集成迭代器。
    if not argv or any(not isinstance(a, str) or "\x00" in a for a in argv):
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise ValueError("Invalid argv")
    if timeout <= 0:
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise ValueError("Timeout must be positive")
    cancel = cancel or CancelToken()
    cancel.check()
    # 条件表达式：条件 argv[0] in {'python', 'python3'} 成立取前面的值，否则取 else 后面的值。
    # 这里的单星号把列表/序列中的各项展开；原顺序保留。
    # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
    actual = [sys.executable, *argv[1:]] if argv[0] in {"python", "python3"} else list(argv)
    # 准备进程环境与输出接收桶：去掉名称含 KEY/TOKEN/SECRET/PASSWORD 的常见变量；这是启发式过
    # 滤。临时文件接输出，避免管道缓冲堵住子进程。
    # 推导式把循环写紧凑：从 os.environ.items() 逐项取出 (k, v)，仅保留满足 not any((word in
    # k.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))) 的项，收集成字典。
    # 推导式把循环写紧凑：从 ('KEY', 'TOKEN', 'SECRET', 'PASSWORD') 逐项取出 word，收集成迭代
    # 器。
    env = {k: v for k, v in os.environ.items() if not any(word in k.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    job = None
    # perf_counter： 读取适合测耗时的单调计时值；用结束减开始，不能拿它当日历日期。
    started = time.perf_counter()
    # 启动机器并装进清理盒：Windows 不打开新可见窗口，尽量建立 Job；其他系统开新进程组。失败时
    # 会用有限的 taskkill 后备。
    # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
    with tempfile.TemporaryFile() as output:
        # Windows 将 CREATE_NO_WINDOW 和 CREATE_NEW_PROCESS_GROUP 用 | 合并为位标志：隐藏子进程窗
        # 口并建立进程组。其他系统用 start_new_session 建独立会话，便于结束时清理整组。
        # 条件表达式：条件 os.name == 'nt' 成立取前面的值，否则取 else 后面的值。
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        # 启动实际命令：actual 是参数列表，cwd 是工作目录，输出汇入文件。接下来等待时仍会检查取消
        # 与超时。
        # 调用时的 ** 把字典按参数名展开；字段 key 的值相当于用 key=值 传给函数。
        process = subprocess.Popen(actual, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, **options)
        # 条件表达式：条件 os.name != 'nt' 成立取前面的值，否则取 else 后面的值。
        cleanup = "process_group" if os.name != "nt" else "taskkill_fallback"
        if os.name == "nt":
            try:
                from forgecode.execution.windows_job import WindowsJob
                job = WindowsJob(process)
                cleanup = "windows_job"
            except OSError:
                pass
        # 看表等待，随时能停：poll 判断是否仍运行；每次等 0.05 秒，循环检查取消与超时。不是一次阻
        # 塞等到天荒地老。
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        timed_out, cancelled = False, False
        # try 中发生匹配的异常会走 except；finally 无论成功、异常或取消都执行，用于收尾。
        try:
            # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
            # poll： 查询进程是否结束；None 表示仍在运行，整数才是退出码。
            while process.poll() is None:
                # 收到停止信号就离开等待循环：标记 cancelled，再 break 跳出。后面的收集和 finally 清理仍要
                # 执行。
                if cancel.cancelled:
                    cancelled = True
                    break
                # perf_counter： 读取适合测耗时的单调计时值；用结束减开始，不能拿它当日历日期。
                if time.perf_counter() - started >= timeout:
                    timed_out = True
                    break
                try:
                    # wait： 等待对象完成；本项目 process.wait 等进程退出，有 timeout 时超过时间抛超时异常。
                    process.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    pass
        # 无论成功、异常、超时或取消，都会来到 finally；不能遗漏仍在运行的子进程。
        finally:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif job:
                job.close()
            # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
            # poll： 查询进程是否结束；None 表示仍在运行，整数才是退出码。
            elif process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
            # poll： 查询进程是否结束；None 表示仍在运行，整数才是退出码。
            if process.poll() is None:
                process.kill()
            # wait： 等待对象完成；本项目 process.wait 等进程退出，有 timeout 时超过时间抛超时异常。
            process.wait()
        # 收集有限输出并返回真状态：2MB 内完整读取，超过则各取 1MB 头尾，中间不可回读。返回退出码
        # 和超时/取消单独标志，不能只看正文是否说成功。
        size = output.seek(0, os.SEEK_END)
        output.seek(0)
        if size <= 2000000:
            text = output.read().decode("utf-8", errors="replace")
        else:
            head = output.read(1000000)
            # seek 的第二项 SEEK_END 表示以末尾为原点；负数向前移动，下面再读最后一百万字节。中间被省
            # 略的内容不会保存在这份捕获结果里。
            output.seek(-1000000, os.SEEK_END)
            text = head.decode("utf-8", errors="replace") + "\n[PROCESS CAPTURE LIMIT: MIDDLE OMITTED]\n" + output.read().decode("utf-8", errors="replace")
    # perf_counter： 读取适合测耗时的单调计时值；用结束减开始，不能拿它当日历日期。
    return {"exit_code": process.returncode, "timed_out": timed_out, "cancelled": cancelled,
            "output": text, "truncated": size > 2000000, "output_bytes": size,
            "cleanup": cleanup, "duration_ms": round((time.perf_counter()-started)*1000, 2)}
