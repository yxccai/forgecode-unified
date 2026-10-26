"""Windows 子进程组清理；Job Object 不提供文件或网络沙箱。
结构与标志参见 Microsoft JOBOBJECT_EXTENDED_LIMIT_INFORMATION 文档。
"""
# 【模块框架】Windows 进程收纳盒：关闭盒子时终止其中进程树。
# 连接位置：command → WindowsJob(process) → finally close
# 流过这里的数据：ctypes Structure 按 Windows ABI 布局字段；KILL_ON_JOB_CLOSE=0x2000。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import ctypes
from ctypes import wintypes as w

# 【类框架 Basic】Windows 原生结构的基础字段布局，字段顺序和 ctypes 类型需要与系统接口一致。
class Basic(ctypes.Structure):
    # ctypes.Structure 把 Python 声明映射成系统需要的内存布局；每个二元组是（字段名，C 类型），
    # 顺序和类型都必须与 Windows 结构对应。DWORD 是 32 位无符号值；c_size_t 的宽度跟当前进程位
    # 数一致。
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", w.DWORD), ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t),
                ("active", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

# 【类框架 IO】Windows 原生输入输出计数结构；这里声明内存布局，不是在执行 Python 文件操作。
class IO(ctypes.Structure):
    # 推导式把循环写紧凑：从 ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes
    # ', 'other_bytes') 逐项取出 name，收集成列表。
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

# 【类框架 Limits】把基础限制、输入输出计数和内存限制组合为 Windows 系统接口要求的结构。
class Limits(ctypes.Structure):
    # Limits 嵌套 Basic 与 IO 两个结构，随后才是内存限制/峰值字段；系统按位置读取这些字段，不
    # 能任意换序。
    _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

# 【类框架 WindowsJob】Windows 进程组的生命周期管理：创建 Job → 绑定子进程 → 关闭时清理进
# 程树。
class WindowsJob:
    # 【函数框架 WindowsJob.__init__】创建 Windows Job 并把刚启动的进程装进去。
    # 输入：subprocess.Popen 对象。 输出：拥有 Job HANDLE 的 WindowsJob，或 OSError。
    # 1. 声明 kernel32 函数参数/返回类型
    # 2. 创建 Job 句柄
    # 3. 设置关闭即终止标志
    # 4. 把进程句柄加入 Job；失败清理后抛异常
    def __init__(self, process):
        # 小例子：测试父进程启动子进程后，关 Job 能清理两者。
        # 留意：ctypes 类型必须与系统接口相符；Job 管进程生命周期，不限制文件和网络权限。
        # WinDLL 加载 Windows 的 kernel32 系统接口；use_last_error=True 让后续能读取调用失败的系统
        # 错误码。
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        # argtypes 声明传给 C 函数的参数类型，restype 声明返回类型；HANDLE 是系统资源句柄，BOOL 是
        # 成功/失败标记。后面为每个接口分别声明，避免地址/整数转换出错。
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.SetInformationJobObject.restype = w.BOOL
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.AssignProcessToJobObject.restype = w.BOOL
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.api.CloseHandle.restype = w.BOOL
        # 先建一个 Job Object 容器；句柄只表示容器存在，子进程还要在下面加入这个容器。
        self.handle = self.api.CreateJobObjectW(None, None)
        limits = Limits()
        # 0x2000 是十六进制标志 KILL_ON_JOB_CLOSE：关闭最后一个 Job 句柄时终止其中的进程。它管理进
        # 程生命周期，不隔离文件或网络。
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
        # 从左往右用 or 短路检查：创建失败、设置限制失败、绑定进程失败，任一成立便清理并报错。9 指
        # 扩展限制信息；byref 传结构地址，sizeof 传结构字节数，process._handle 是已有的 Windows 进
        # 程句柄。
        if not self.handle or not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not self.api.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))):
            self.close()
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise OSError(ctypes.get_last_error(), "Unable to attach Windows Job Object")

    # 【函数框架 WindowsJob.close】安全释放 Job 句柄，触发进程清理。
    # 输入：当前 self.handle。 输出：句柄置空；无值返回。
    # 1. 有 handle 才 CloseHandle
    # 2. 置为 None
    # 3. 再次调用不会重复关闭旧句柄
    def close(self):
        # 小例子：command 的 finally 调用 job.close。
        # 留意：要在 finally 调用；任务成功、超时、取消都需释放。
        # getattr： 读取可能存在的属性；第三个参数是属性不存在时的默认值。
        if getattr(self, "handle", None):
            self.api.CloseHandle(self.handle)
            self.handle = None
