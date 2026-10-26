"""用户预授权的验证命令；判断依据为真实退出码、超时和取消。"""
# 【模块框架】质检员：用已确认的测试命令判断是否通过。
# 连接位置：Runtime.baseline/verify → Verification.run → command
# 流过这里的数据：passed 要求 exit_code=0 且未超时、未取消。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from forgecode.execution.process import command

# 【类框架 Verification】可信测试的执行入口：运行预授权命令 → 读取退出码/超时/取消 → 决定
# passed。
class Verification:
    # 【函数框架 Verification.__init__】给质检员设定工作台和停止铃。
    # 输入：root、cancel。 输出：Verification。
    # 1. 保存工作根
    # 2. 共用取消令牌
    # 3. 等待 run
    def __init__(self, root, cancel):
        # 小例子：验证在 Agent 当前 worktree 运行。
        # 留意：它不替用户猜可信验证命令。
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        self.root, self.cancel = root, cancel

    # 【函数框架 Verification.run】运行测试并添加可检查的通过标记。
    # 输入：预授权 argv。 输出：命令结果加 passed。
    # 1. 调用共同 command
    # 2. 同时检查退出码、超时、取消
    # 3. 写 passed 并返回
    def run(self, argv):
        # 小例子：exit_code=0 但 timed_out=True → passed=False。
        # 留意：只有输出含 'OK' 不够；通过标记由进程结果决定。
        # 运行可信的验证命令：command 执行用户认可的测试命令。模型说完成不能替代这次真实运行。
        result = command(self.root, argv, cancel=self.cancel)
        # 把退出结果变成通过判断：退出码为 0、没有超时、没有取消，三项都成立才记 passed。
        result["passed"] = result["exit_code"] == 0 and not result["timed_out"] and not result["cancelled"]
        return result
