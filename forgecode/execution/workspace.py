"""复用原项目的有效实现，依赖已改为统一包；详见 docs/architecture.md。"""
# 【模块框架】独立修理台与交付包：从 HEAD 开 worktree，把修改导成 patch。
# 连接位置：batch/eval/learn → Workspace.create/load → Agent → export
# 流过这里的数据：manifest 记 root/worktree/head/verify；patch 记录跟踪及新文件的变更。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import json
from pathlib import Path
import re
import subprocess

# 【函数框架 git】用固定 argv 调用 Git，并把非零退出视为异常。
# 输入：root、若干 Git 参数、strip 开关。 输出：UTF-8 输出文本。
# 1. subprocess.run 捕获输出、限制 30 秒
# 2. 非零退出抛 RuntimeError
# 3. 解码，按需去边缘空白
def git(root, *args, strip=True):
    # 小例子：git(root,'rev-parse','HEAD') 取得当前提交。
    # 留意：strip=False 对 patch 必要，不能把有意义的换行删掉。
    # 这里的单星号把列表/序列中的各项展开；原顺序保留。
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, timeout=30)
    if result.returncode:
        # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
        raise RuntimeError(result.stderr.decode(errors='replace'))
    output = result.stdout.decode('utf-8', errors='replace')
    # 条件表达式：条件 strip 成立取前面的值，否则取 else 后面的值。
    return output.strip() if strip else output

# 【类框架 Workspace】一次仓库任务的文件现场：新 worktree/加载已有现场 → 在其中运行 → 导出
#  patch。
class Workspace:

    # 【函数框架 Workspace.__init__】定义一项仓库任务的备用台和清单位置。
    # 输入：仓库 root 与 thread。 输出：Workspace，尚未创建 worktree。
    # 1. 校验 thread 字符与长度
    # 2. 确认 root 是 Git 根
    # 3. 计算 .forgecode/worktrees 与 tasks 路径
    def __init__(self, root, thread):
        # 小例子：thread='lesson' 对应 worktrees/lesson。
        # 留意：thread 被用于文件名，因此不能含任意路径片段。
        # fullmatch： 整个文字都必须符合指定模式；不能只让其中一小段符合，编号范围见本行模式。
        if not re.fullmatch('[a-zA-Z0-9_-]{1,80}', thread):
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError('Thread must contain 1-80 letters, numbers, underscores or hyphens')
        # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
        self.root = Path(root).resolve()
        # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
        if Path(git(self.root, 'rev-parse', '--show-toplevel')).resolve() != self.root:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError('--repo must be the Git repository root')
        self.thread = thread
        self.data = self.root / '.forgecode'
        self.path = self.data / 'worktrees' / thread
        self.manifest = self.data / 'tasks' / f'{thread}.json'

    # 【函数框架 Workspace.create】从干净仓库 HEAD 创建独立工作台。
    # 输入：用户确认的 verify argv。 输出：metadata，并实际创建 worktree 与 manifest。
    # 1. 拒绝已存在任务
    # 2. 拒绝原仓库未提交变更并要求验证命令
    # 3. 取 HEAD，创建 detached worktree
    # 4. 写入位置、起点和验证命令
    def create(self, verify):
        # 小例子：原 app.py 仍 a-b，工作台可被改成 a+b。
        # 留意：worktree 不复制当前未提交内容；它隔离文件版本，不隔离获批进程权限。
        # 任务名与原仓库必须清楚：已存在不覆盖；原未提交变化不猜着复制。验证命令也必须明确。
        if self.manifest.exists() or self.path.exists():
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError('Thread already exists; resume it or choose a new ID')
        if git(self.root, 'status', '--porcelain', '--', '.', ':(exclude).forgecode'):
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError('Commit or stash repository changes first; worktree starts at HEAD')
        if not verify:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError('An explicit verification command is required')
        # 从固定提交搭备用台：worktree add --detach 从 HEAD 开出独立目录，原分支不自动推进。
        head = git(self.root, 'rev-parse', 'HEAD')
        # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        git(self.root, 'worktree', 'add', '--detach', str(self.path), head)
        # 把恢复位置写入清单：root/worktree/head/verify 让 load 与入口核对同一个任务。
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            'root': str(self.root),
            'worktree': str(self.path),
            'head': head,
            'verify': verify
        }
        # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        self.manifest.write_text(json.dumps(metadata, indent=2))
        return metadata

    # 【函数框架 Workspace.load】恢复前核对备用台有没有被替换。
    # 输入：已有 manifest。 输出：通过检查的 metadata。
    # 1. 读取 JSON
    # 2. 核对原根与 worktree 位置
    # 3. 检查目录存在且 Git 顶层一致
    def load(self):
        # 小例子：worktree 被移动/替换时拒绝恢复。
        # 留意：不能仅凭 manifest 存在就相信任意目录。
        # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
        # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
        metadata = json.loads(self.manifest.read_text())
        if metadata['root'] != str(self.root) or metadata['worktree'] != str(self.path):
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError('Workspace metadata mismatch')
        # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
        if not self.path.is_dir() or Path(git(self.path, 'rev-parse', '--show-toplevel')).resolve() != self.path:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError('Worktree missing or replaced')
        return metadata

    # 【函数框架 Workspace.export】导出实际修改，包括新建文件。
    # 输入：当前 worktree 与任务目录。 输出：保存的 patch 路径。
    # 1. 读取相对 HEAD 的 binary diff
    # 2. 枚举未跟踪文件并经路径守卫过滤
    # 3. 用 --no-index 与空文件比较补新文件
    # 4. 写 patch，不自动应用到原仓库
    def export(self):
        # 小例子：修 app.py 并新建 helper.py，二者都进入交付 patch。
        # 留意：git diff 默认漏掉未跟踪文件；新文件要另导出，退出码 1 在 diff 中表示有差异。
        # 先收集跟踪文件变更：对 HEAD 做 binary diff，并禁用外部 diff/textconv。
        patch = git(self.path, '--no-pager', 'diff', '--binary', '--no-ext-diff', '--no-textconv', 'HEAD', strip=False)
        # 新文件另取清单：ls-files --others 用零分隔识别名称，路径守卫排除内部文件/链接。
        from forgecode.execution.paths import PathGuard
        guard = PathGuard(self.path)
        untracked = git(self.path, 'ls-files', '--others', '--exclude-standard', '-z', strip=False).split('\x00')
        for name in untracked:
            if not name:
                continue
            try:
                path = guard.path(name)
                if not path.is_file() or (self.path / name).is_symlink() or '__pycache__' in path.parts:
                    continue
            except ValueError:
                continue
            # 把新文件与空文件比较：--no-index 退出1表示有差异，可以接到 patch；其他异常不能忽略。
            result = subprocess.run(['git', 'diff', '--no-index', '--binary', '--no-ext-diff', '--', '/dev/null', name], cwd=self.path, capture_output=True, timeout=30)
            if result.returncode not in (0, 1):
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise RuntimeError('Unable to export new file')
            patch += '\n' + result.stdout.decode('utf-8', errors='replace')
        # 保存交付包，不自动应用：写到任务 .patch 文件并返回位置，原工作区还留着方便检查。
        target = self.data / 'tasks' / f'{self.thread}.patch'
        # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
        target.write_text(patch, encoding='utf-8', newline='')
        return target
