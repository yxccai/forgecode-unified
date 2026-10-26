"""唯一命令导航；入口复用组件，不实现另一个 Agent。"""
# 【模块框架】门口路牌：把命令分发到 chat/run/learn/eval 等入口。
# 连接位置：python -m forgecode / forge → cli.main → 对应入口 → create_runtime
# 流过这里的数据：argv 是命令后的参数列表，tail 是去掉入口名的剩余参数。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import argparse
import sys

# 【函数框架 main】识别入口名字，将剩余参数交给对应功能。
# 输入：argv 或 sys.argv[1:]。 输出：对应入口的返回值，或 argparse 帮助/错误。
# 1. 空参数与 chat 进入交互，临时适配 sys.argv
# 2. learn/run/eval/report 延迟导入后转发 tail
# 3. configure 准备目录再配置
# 4. 未知命令交 argparse 校验
def main(argv=None):
    # 小例子：start.ps1 learn → learning.main([])；start.ps1 eval --offline → runner.main(['--
    # offline'])。
    # 留意：finally 恢复 sys.argv，避免影响同一进程后面的调用；此函数不做 Agent 决策。
    # 条件表达式：条件 argv is None 成立取前面的值，否则取 else 后面的值。
    # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
    # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] == "chat":
        from forgecode.interactive import main as chat
        original = sys.argv
        # try 中发生匹配的异常会走 except；finally 无论成功、异常或取消都执行，用于收尾。
        try:
            # 这里的单星号把列表/序列中的各项展开；原顺序保留。
            # 条件表达式：条件 args 成立取前面的值，否则取 else 后面的值。
            # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
            sys.argv = ["forge", *(args[1:] if args else [])]
            chat()
        finally:
            sys.argv = original
        return
    # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
    # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
    command, tail = args[0], args[1:]
    if command == "learn":
        from forgecode.learning import main as learn
        return learn(tail)
    if command == "run":
        from forgecode.batch import main as run
        return run(tail)
    if command == "eval":
        from forgecode.evaluation.runner import main as evaluate
        return evaluate(tail)
    if command == "report":
        from forgecode.evaluation.report import main as report
        return report(tail)
    if command == "configure":
        from forgecode.config import configure
        from pathlib import Path
        # 条件表达式：条件 tail 成立取前面的值，否则取 else 后面的值。
        path = Path(tail[0] if tail else ".forgecode/config.local.json")
        # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
        path.parent.mkdir(parents=True, exist_ok=True)
        return configure(str(path))
    parser = argparse.ArgumentParser(description="ForgeCode 统一 Agent：learn / chat / run / eval / report")
    parser.add_argument("command", choices=("learn", "chat", "run", "eval", "report", "configure"), nargs="?")
    parser.parse_args(args)

# 【函数框架 batch_main】兼容单次仓库任务的命令入口。
# 输入：进程命令行参数。 输出：batch.main 的返回/退出。
# 1. 延迟导入 batch.main
# 2. 直接调用
# 3. 任务逻辑仍只有一份
def batch_main():
    # 小例子：独立 batch 可执行入口仍执行相同仓库任务实现。
    # 留意：转发入口不是第二个 Runtime。
    from forgecode.batch import main as run
    return run()
