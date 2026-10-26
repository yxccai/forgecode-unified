"""只处理路径；检索、记忆与工具共同使用，避免相互依赖。"""
# 【模块框架】围栏：文件工具、检索与记忆只能处理许可范围。
# 连接位置：Repository/Memory/Files → PathGuard.path/files → 文件系统
# 流过这里的数据：root 固定为已存在绝对目录；拒绝 ..、绝对路径、内部数据、符号链接/连接点。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import os
from pathlib import Path

# 【类框架 PathGuard】相对路径的守卫：排除内部与越界路径 → 拒绝链接/连接点 → 再核对解析后
# 的真实位置。
class PathGuard:
    # 【函数框架 PathGuard.__init__】设定操作围栏的根。
    # 输入：root 路径。 输出：PathGuard 或根不存在错误。
    # 1. resolve 成绝对目录
    # 2. 检查 is_dir
    # 3. 保存根供每次核验
    def __init__(self, root):
        # 小例子：样例 worktree 根作为所有文件相对路径的起点。
        # 留意：根要先存在；路径守卫不是自动创建任意工作目录的工具。
        # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Repository directory does not exist")

    # 【函数框架 PathGuard.path】将相对名字转换为围栏内的真实路径。
    # 输入：name 相对路径。 输出：合法绝对 Path，或 ValueError。
    # 1. 拒绝绝对路径和 ..
    # 2. 排除 .git/.forgecode/.venv/配置/环境文件
    # 3. 逐段拒绝符号链接与连接点
    # 4. resolve 后再次确认仍在 root 内
    def path(self, name):
        """先检查原始路径，再解析；拒绝越界、符号链接和内部数据。"""
        # 小例子：app.py 可通过；../secret.txt、.env、外链目录拒绝。
        # 留意：只检查字符串前缀不可靠；这里只约束走此函数的文件操作，获批原生命令不是完整沙箱。
        # 先查用户写下的路径形式：绝对路径和 .. 直接拒绝；先不解析它们到别处。
        raw = Path(name)
        if raw.is_absolute() or ".." in raw.parts:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Path escapes repository")
        # 内部数据不能变成模型材料：大小写无关过滤 Git、内部数据库、环境和本地配置等。
        # 推导式把循环写紧凑：从 raw.parts 逐项取出 part，收集成迭代器。
        if any(part.lower() in {".git", ".forgecode", ".venv", "__pycache__", "config.local.json"} or part.lower().startswith(".env") for part in raw.parts):
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Private or internal path")
        # 逐段查跳转链接：符号链接/连接点可能把看似厂内路径带去厂外，因此逐段拒绝。
        current = self.root
        for part in raw.parts:
            current = current / part
            # hasattr： 确认属性是否存在；常用来适配不同库对象是否支持 close 等方法。
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Symbolic links are not repository files")
        # 最后再核对解析后的真实位置：is_relative_to 检查是否在 root 内，比字符串前缀比较可靠。
        target = (self.root / raw).resolve()
        # is_relative_to： 检查一个路径是否处于另一个路径下面，返回布尔值。
        if not target.is_relative_to(self.root):
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Path escapes repository")
        return target

    # 【函数框架 PathGuard.files】在没有合适 Git 索引时受控遍历文件。
    # 输入：self.root。 输出：逐个 yield 许可文件。
    # 1. os.walk 时删去隐藏/构建/依赖目录
    # 2. 跳过隐藏文件与本地配置
    # 3. 每项再调用 path 核验，限制大小并跳过错误
    def files(self):
        """忽略隐藏目录、生成文件、链接和大文件。"""
        # 小例子：node_modules 不进入，app.py 会被列出。
        # 留意：dirs[:] 修改 walk 即将进入的目录列表；dirs=新列表不能控制原遍历。
        for folder, dirs, names in os.walk(self.root):
            # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
            # 推导式把循环写紧凑：从 dirs 逐项取出 d，仅保留满足 not d.startswith('.') and d not in {'
            # node_modules', '__pycache__', 'dist', 'build'} and (not (Path(folder) / d).is_symlink())
            #  的项，收集成迭代器。
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__", "dist", "build"} and not (Path(folder) / d).is_symlink())
            for name in sorted(names):
                if name.startswith(".") or name == "config.local.json":
                    continue
                try:
                    # relative_to： 取相对于指定根目录的路径；不在该根下会报错，不能用字符串前缀冒充这个检查。
                    p = self.path(str((Path(folder) / name).relative_to(self.root)))
                    if p.is_file() and p.stat().st_size <= 500000:
                        # yield 暂时交出一个值并保留执行位置；生成器下次继续，contextmanager 在 with 退出后继续收
                        # 尾。
                        yield p
                except (ValueError, OSError):
                    continue
