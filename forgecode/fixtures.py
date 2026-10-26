"""受控样例 Git 仓库；只在调用者指定的新目录内创建。"""
# 【模块框架】教学练习场：创建一个有明确错误和测试的新 Git 仓库。
# 连接位置：learning.demo/eval → fixture/init_repository → Workspace
# 流过这里的数据：样例 app.py 故意 a-b；公开测试期望正常加法及负数加法。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from pathlib import Path
from forgecode.execution.workspace import git

# 【函数框架 init_repository】把新目录变成可建立 worktree 的独立 Git 样例。
# 输入：已经准备文件的 root。 输出：初始 Git 提交。
# 1. git init 并关闭换行自动转换
# 2. 设置空 hooks 目录
# 3. 添加文件，显式 fixture 身份提交且不签名
# 4. 核对 Git 根
def init_repository(root):
    # 小例子：参考提交成为 Workspace.create 的 HEAD 起点。
    # 留意：配置仅写新样例 Git，不修改用户全局 Git 身份。
    root = Path(root)
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    empty_hooks = root / ".git" / "no-hooks"
    # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
    empty_hooks.mkdir(exist_ok=True)
    git(root, "config", "core.hooksPath", str(empty_hooks))
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "-c", "user.name=Fixture",
        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise ValueError("Fixture is not an independent Git repository")

# 【函数框架 fixture】生成最小但真实可测试的错误加法仓库。
# 输入：新样例 root。 输出：仓库 Path。
# 1. 建目录
# 2. 写 add=a-b、两条 unittest 与 gitignore
# 3. 调用 init_repository
def fixture(root):
    # 小例子：add(2,3) 应为 5，负数例应为 -5，原实现均不通过。
    # 留意：这个 fixture 本来就错误；不能把修好工作区自动写回原样例。
    root = Path(root)
    # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
    root.mkdir(parents=True, exist_ok=True)
    # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
    (root / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
    (root / "test_app.py").write_text("import unittest\nfrom app import add\nclass Test(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2, 3), 5)\n def test_negative(self): self.assertEqual(add(-2, -3), -5)\n", encoding="utf-8")
    # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
    (root / ".gitignore").write_text(".forgecode/\n__pycache__/\n", encoding="utf-8")
    init_repository(root)
    return root
