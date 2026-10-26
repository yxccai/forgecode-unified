"""取消信号可由界面、线程或测试触发；工具与模型共用它。"""
# 【模块框架】全厂共用的停止铃：模型与工具同时检查它。
# 连接位置：入口/界面 → cancel → Runtime/Model/process.check
# 流过这里的数据：threading.Event 保存取消信号；reason 保存说明；Cancelled 是特定异常类型。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from threading import Event

# 【类框架 Cancelled】专门表示取消的异常类型。抛出它会转入 except 分支；不是测试通过或普通
# 完成。
class Cancelled(Exception):
    pass

# 【类框架 CancelToken】大家共用一个停止信号：cancel 设置 Event，cancelled 查询，check 在
# 需要处抛异常。
class CancelToken:
    # 【函数框架 CancelToken.__init__】创建初始未取消的停止信号。
    # 输入：无外部输入。 输出：CancelToken 对象。
    # 1. 创建 Event
    # 2. reason 置空
    # 3. 等待取消或检查
    def __init__(self):
        # 小例子：app 装配时创建一次，传给模型与工具。
        # 留意：Event 适合跨线程标记，不需要靠反复传布尔值副本。
        self._event = Event()
        self.reason = ""

    # 【函数框架 CancelToken.cancel】拉响停止铃并记录原因。
    # 输入：reason 文本。 输出：Event 置为已设置。
    # 1. 保存 reason
    # 2. 调用 Event.set
    # 3. 后续检查能读到
    def cancel(self, reason="Cancelled by user"):
        # 小例子：cancel('用户取消') 后 command 轮询观察到 cancelled=True。
        # 留意：设置标记不会魔法般瞬间中断任何代码；各执行环节要主动检查或清理。
        self.reason = reason
        self._event.set()

    # 【函数框架 CancelToken.reset】新一轮任务前放下停止铃。
    # 输入：当前令牌。 输出：取消标记清除、reason 为空。
    # 1. Event.clear
    # 2. 清空原因
    # 3. 允许下一轮正常开始
    def reset(self):
        # 小例子：Session 新用户轮次开始前 reset。
        # 留意：恢复未知副作用不能靠 reset 解决，必须先核对回执。
        self._event.clear()
        self.reason = ""

    # 【函数框架 CancelToken.cancelled】只读查询是否已拉响停止铃。
    # 输入：当前 Event。 输出：布尔值。
    # 1. 调用 is_set
    # 2. 返回当前标记
    # 3. 供路由或轮询判断
    # @property：调用者像读字段一样使用它，读取时仍会执行这个方法。
    @property
    def cancelled(self):
        # 小例子：if cancel.cancelled: 停止继续发请求。
        # 留意：@property 让 cancelled 看起来像字段，读取时实际调用方法。
        return self._event.is_set()

    # 【函数框架 CancelToken.check】把取消信号转换成能向上层传播的异常。
    # 输入：当前标记与 reason。 输出：未取消就无值返回；取消则抛 Cancelled。
    # 1. 读取 cancelled
    # 2. 已取消就 raise
    # 3. 上层捕捉后写明确状态
    def check(self):
        # 小例子：模型流接收下一片前 check，取消就进入 finally 关闭流。
        # 留意：raise 与 return 不同：raise 会跳到最近匹配的 except。
        if self.cancelled:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise Cancelled(self.reason)
