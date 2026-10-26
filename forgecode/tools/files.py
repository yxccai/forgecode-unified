"""真实文件操作；精确匹配和原子替换让修改可核对。"""
# 【模块框架】双手中的文件工具：真实读、搜、改，修改要求唯一精确匹配。
# 连接位置：ToolRegistry.execute → Files 方法 → PathGuard/真实文件系统
# 流过这里的数据：read_file 带行号；prepare_edit 产出 before/after 指纹与 replacement；
# edit_file 原子替换。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import os
from pathlib import Path
import tempfile
from forgecode.execution.receipts import fingerprint
from forgecode.execution.process import command

# 【类框架 Files】真实文件能力：受控读/搜/列文件；编辑先定位唯一旧片段，再按核对后的方案写
# 入。
class Files:
    # 【函数框架 Files.__init__】给文件工具安装共同守卫和目录来源。
    # 输入：guard、repository。 输出：Files 对象。
    # 1. 保存 guard
    # 2. 保存 repository
    # 3. 所有文件方法共用它们
    def __init__(self, guard, repository):
        # 小例子：搜索与读取只在当前工作区进行。
        # 留意：不直接接受无限范围的绝对路径。
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        self.guard, self.repository = guard, repository

    # 【函数框架 Files.read_file】读出一页带行号的源码。
    # 输入：path 与从 1 开始的 start。 输出：最多 200 行文本。
    # 1. 路径守卫确认范围
    # 2. 拒绝超过 500KB 的文件
    # 3. UTF-8 读文本，按行号选一页
    def read_file(self, path: str, start: int = 1):
        """Read at most 200 numbered lines of a repository file."""
        # 小例子：read_file('app.py',1) → '1: def add...2: return a-b'。
        # 留意：start 是源码行号，不是从 0 开始的列表下标；下一页需新调用。
        target = self.guard.path(path)
        if target.stat().st_size > 500000:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("File exceeds 500 KB read limit")
        # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
        lines = target.read_text(encoding="utf-8").splitlines()
        # 推导式把循环写紧凑：从 enumerate(lines) 逐项取出 (i, line)，仅保留满足 start <= i + 1 <
        # start + 200 的项，收集成迭代器。
        return "\n".join(f"{i+1}: {line}" for i, line in enumerate(lines) if start <= i+1 < start+200)

    # 【函数框架 Files.list_files】给模型看一页可访问的文件清单。
    # 输入：Repository.paths 的候选流。 输出：最多 200 个相对路径的文本。
    # 1. islice 只取前 200 项
    # 2. 转成相对根的名字
    # 3. 以换行连接
    def list_files(self):
        """List up to 200 permitted repository file paths."""
        # 小例子：返回 app.py、test_app.py；模型再选 app.py 读取。
        # 留意：没有把所有文件正文送进模型，清单只是选书目录。
        from itertools import islice
        # 推导式把循环写紧凑：从 islice(self.repository.paths(), 200) 逐项取出 p，收集成迭代器。
        # relative_to： 取相对于指定根目录的路径；不在该根下会报错，不能用字符串前缀冒充这个检查。
        return "\n".join(str(p.relative_to(self.guard.root)) for p in islice(self.repository.paths(), 200))

    # 【函数框架 Files.search】在可读文件里找字面文本位置。
    # 输入：query 字符串。 输出：最多 80 条 path:line:text。
    # 1. 遍历候选文件并逐行读取
    # 2. 子串匹配 query，截取单行预览
    # 3. 够 80 条停止，编码/读取错误跳过
    def search(self, query: str):
        """Find literal text; return paths and line numbers."""
        # 小例子：search('a - b') 定位 app.py 的第二行。
        # 留意：这是字面匹配，区分大小写，不是语义搜索或正则执行。
        matches = []
        for p in self.repository.paths():
            try:
                # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if query in line:
                        # relative_to： 取相对于指定根目录的路径；不在该根下会报错，不能用字符串前缀冒充这个检查。
                        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
                        matches.append(f"{p.relative_to(self.guard.root)}:{i}: {line[:300]}")
                    if len(matches) >= 80:
                        return "\n".join(matches)
            except (UnicodeError, OSError):
                continue
        return "\n".join(matches)

    # 【函数框架 Files.prepare_edit】先计算小修改会得到什么，不先动文件。
    # 输入：path、old、new。 输出：path、before、after、完整 replacement。
    # 1. 限制文件和替换大小
    # 2. 旧文件要求 old 非空且只出现一次，新文件要求 old 为空
    # 3. 构造 replacement，计算前后 SHA256
    def prepare_edit(self, path, old, new):
        # 小例子：a-b 只出现一次 → 计算改成 a+b 的完整内容和 after 指纹。
        # 留意：old 出现两次不能猜改哪处；应重新读出更完整片段。
        # 守卫与大小限制：不能把任意绝对路径或超大替换交给文件工具。
        target = self.guard.path(path)
        if len(new) > 100000:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Replacement too large")
        # 旧文件与创建文件的合同不同：旧文件 old 必须非空且恰好匹配1次；新文件 old 必须空。否则错
        # 误直接返回上层。
        if target.exists():
            if target.stat().st_size > 500000:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("File too large")
            # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
            text = target.read_text(encoding="utf-8")
            # 已有文件必须唯一匹配：old 为空或出现次数不是 1，就拒绝。两处都匹配时，程序不能猜该改哪处。
            if not old or text.count(old) != 1:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("old must match exactly once; read the file again")
            # 准备替换后的内容：得到 replacement 还不是写入。此方法准备内容和前后指纹，让执行与恢复能
            # 核对事实。
            replacement = text.replace(old, new, 1)
        else:
            if old:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("File does not exist; creation requires empty old")
            replacement = new
        # 只算计划，不改现实：replacement 的完整字节内容用来算 after。before/after 给回执恢复核对，
        # 不代表有并发锁。
        import hashlib
        # hexdigest： 把摘要转成十六进制文字，便于作为字典值或保存编号。
        # sha256： 把内容算成固定摘要；用来比对版本或参数，不是加密后可以再还原的文字。
        return {
            "path": path,
            "before": fingerprint(target),
            "after": hashlib.sha256(replacement.encode()).hexdigest(),
            "replacement": replacement
        }

    # 【函数框架 Files.edit_file】把计算好的修改一次性替换到目标位置。
    # 输入：path、old、new。 输出：edited 路径和预期指纹。
    # 1. 调用 prepare_edit
    # 2. 在同目录写临时文件
    # 3. 保留原文件权限，os.replace 原子替换
    # 4. finally 清理未替换的临时文件
    def edit_file(self, path: str, old: str, new: str):
        """Replace one exact unique occurrence, or create with empty old."""
        # 小例子：先写临时完整 a+b，再用 replace 替换 app.py。
        # 留意：原子替换避免读到半个文件，但不等于数据库与 checkpoint 同步事务，也不是并发编辑锁。
        # 先准备唯一精确修改：准备成功才建目标父目录；plan 持有完整替换内容。
        plan = self.prepare_edit(path, old, new)
        target = self.guard.path(path)
        # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        # 写同目录临时完整文件：同目录让原子替换更可靠；先保存新内容，存在原文件时保留其模式。
        # try 中发生匹配的异常会走 except；finally 无论成功、异常或取消都执行，用于收尾。
        try:
            # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".forgecode-edit-", delete=False) as stream:
                temp_path = Path(stream.name)
                stream.write(plan["replacement"].encode())
            if target.exists():
                temp_path.chmod(target.stat().st_mode)
            # 这里是 os.replace，作用是用已写好的临时文件替换目标路径；这才改变真实文件。它与
            # prepare_edit 里的 text.replace 生成新文字不同；plan 保存了预期的前后指纹，供回执核对。
            os.replace(temp_path, target)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()
        return {"edited": path, "fingerprint": plan["after"]}

    # 【函数框架 Files.git_diff】用固定 Git 命令读取修改证据。
    # 输入：当前 guard.root。 输出：diff 与 status 两份命令结果。
    # 1. 执行禁用外部 diff/textconv 的 git diff
    # 2. 读取 git status --short
    # 3. 组合结果
    def git_diff(self):
        """Show tracked diff and untracked paths; does not execute model commands."""
        # 小例子：显示 '- return a-b' 和 '+ return a+b'。
        # 留意：git diff 主要看跟踪文件；新文件还需要 status，导出 patch 另由 Workspace 处理。
        return {
            "diff": command(self.guard.root, ["git", "--no-pager", "diff", "--no-ext-diff", "--no-textconv"]),
            "status": command(self.guard.root, ["git", "status", "--short"])
        }
