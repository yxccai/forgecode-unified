"""复用原项目的有效实现，依赖已改为统一包；详见 docs/architecture.md。"""
# 【模块框架】图书管理员：用文件名、符号和词匹配找候选位置。
# 连接位置：ContextBuilder.evidence / repo_map → Repository.select → index → paths
# 流过这里的数据：每个记录含 path、symbols、imports、行数；索引中的词集合只用于评分，输出
# 去掉它。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import ast
from copy import deepcopy
import hashlib
import re
import subprocess
from pathlib import Path
from forgecode.execution.paths import PathGuard

# 【函数框架 terms】让任务与源码使用同一套词：英文标识词 + 相邻两个汉字。
# 输入：普通文本。 输出：去掉英文常见词的 set。
# 1. 保留完整标识词，同时拆开 computeInvoiceTotal / HTTPServer
# 2. 中文连续文字每次取相邻两个字（不是词典分词）
# 3. 合并去重，剔除 fix/file/test 等英文常见词
def terms(text):
    # 原来把 computeInvoiceTotal 直接变小写，会失去单词边界。
    # 先在小写/数字→大写处插空格；再把 HTTPServer 拆成 HTTP Server。
    separated = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    separated = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', separated)
    # 完整词也保留：精确查询 computeInvoiceTotal 与查询 Invoice Total 都能找到它。
    words = set(re.findall(r'[a-zA-Z][a-zA-Z0-9]*', (text + ' ' + separated).lower()))
    for block in re.findall(r'[\u4e00-\u9fff]+', text):
        # “发票总额”→“发票、票总、总额”。这叫二元字片；只认识字面重合，不会自动中英翻译。
        if len(block) == 1:
            words.add(block)
        else:
            words.update(block[i:i+2] for i in range(len(block)-1))
    return words - {'the', 'and', 'with', 'this', 'that', 'from', 'test', 'tests', 'file', 'fix'}

# 【类框架 Repository】仓库定位器：枚举许可文件 → 提取 Python 符号 → 按任务词匹配并排序。
class Repository:

    # 【函数框架 Repository.__init__】确定检索根并安装路径守卫。
    # 输入：root。 输出：Repository 对象。
    # 1. resolve 固定绝对根
    # 2. 创建 PathGuard
    # 3. 安装实例内内容指纹缓存；上限按缓存对应的源码字节计，不等于 Python 堆内存
    def __init__(self, root, cache_bytes=8000000):
        # 小例子：root 指样例 worktree，索引不会自动遍历电脑其他项目。
        # 留意：检索也必须限制文件范围，不能只让编辑工具受约束。
        # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
        self.root = Path(root).resolve()
        self.guard = PathGuard(root)
        if cache_bytes < 0:
            raise ValueError('Cache source-byte budget must be nonnegative')
        self.cache_bytes = cache_bytes
        self._cache = {}
        self.index_stats = {}

    # 【函数框架 Repository.paths】枚举当前仓库可读的候选文件。
    # 输入：当前 root 与 Git 状态。 输出：逐个 yield Path。
    # 1. 确认当前目录恰是 Git 根，否则用受控遍历
    # 2. 用 Git 的零分隔列表读已跟踪/未忽略文件
    # 3. 去重排序，拒绝链接、大文件与不合法路径
    def paths(self):
        # 嵌套普通目录可能位于另一个 Git 仓库中；只使用当前根的 Git 索引。
        # 小例子：app.py 可入索引；.forgecode 内部数据库被路径守卫排除。
        # 留意：yield 是逐个产生值；嵌套目录不能误用父仓库索引。
        top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=self.root, capture_output=True, timeout=10)
        # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
        if top.returncode or Path(top.stdout.decode('utf-8', errors='replace').strip()).resolve() != self.root:
            yield from self.guard.files()
            return
        result = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard', '-z'], cwd=self.root, capture_output=True, timeout=10)
        if result.returncode == 0:
            for name in sorted(set(result.stdout.decode('utf-8', errors='replace').split('\x00'))):
                if not name:
                    continue
                try:
                    path = self.guard.path(name)
                    if path.is_file() and (not (self.root / name).is_symlink()) and (path.stat().st_size <= 500000):
                        # yield 暂时交出一个值并保留执行位置；生成器下次继续，contextmanager 在 with 退出后继续收
                        # 尾。
                        yield path
                except (ValueError, OSError):
                    continue
        else:
            yield from self.guard.files()

    # 【函数框架 Repository.index】把候选文件整理成目录卡，Python 文件额外提取语法结构。
    # 输入：paths() 的文件。 输出：最多 5000 个记录。
    # 1. 重新枚举许可文件，读取有上限的内容并计算指纹
    # 2. 指纹未变就复用目录卡；变化才重新提词、解析 AST
    # 3. 仅把当前存在且装得下的卡留在下一轮缓存，返回独立副本
    def index(self):
        # 像图书馆复用没改版的目录卡：仍检查每本书，但不反复拆解同一版的语法。
        # 不仅看 mtime/文件大小：同大小改写、恢复时间戳或切分支，也必须识别正文变化。
        records, next_cache = [], {}
        stats = {'cache_hits': 0, 'rebuilt_files': 0, 'parsed_python': 0,
                 'read_bytes': 0, 'cached_files': 0, 'cached_source_bytes': 0}
        for path in self.paths():
            if len(records) >= 5000:
                break
            try:
                # 多读 1 字节是为了发现超限，不把一个突然长大的文件全部装进内存。
                with path.open('rb') as stream:
                    content = stream.read(500001)
                if len(content) > 500000:
                    continue
                source = content.decode('utf-8')
            except (UnicodeError, OSError):
                continue
            stats['read_bytes'] += len(content)
            digest = hashlib.sha256(content).hexdigest()
            cached = self._cache.get(path)
            if cached and cached[0] == digest:
                record = cached[1]
                stats['cache_hits'] += 1
            else:
                stats['rebuilt_files'] += 1
                symbols, imports = [], []
                if path.suffix == '.py':
                    stats['parsed_python'] += 1
                    try:
                        # AST 只解析结构，不执行源码；语法错误时仍保留文字线索。
                        for node in ast.walk(ast.parse(source)):
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                                symbols.append({'name': node.name, 'line': node.lineno, 'kind': type(node).__name__})
                            elif isinstance(node, ast.Import):
                                imports.extend(alias.name for alias in node.names)
                            elif isinstance(node, ast.ImportFrom) and node.module:
                                imports.append(node.module)
                    except SyntaxError:
                        pass
                record = {'path': str(path.relative_to(self.root)), 'symbols': symbols,
                          'imports': imports, 'lines': len(source.splitlines()),
                          'terms': terms(source), 'sha256': digest}
            # 8 MB 是“这些卡对应的源码总字节数”上限；不缓存源码原文，也不持有 AST 树。
            if self.cache_bytes and stats['cached_source_bytes'] + len(content) <= self.cache_bytes:
                next_cache[path] = (digest, record)
                stats['cached_source_bytes'] += len(content)
            # 深拷贝连嵌套 symbols 列表都分开，调用者改输出不会污染下一轮缓存。
            records.append(deepcopy(record))
        # 删除、改名或不再获准访问的文件不在 next_cache 中，旧卡在这里一并淘汰。
        self._cache = next_cache
        stats['cached_files'] = len(next_cache)
        self.index_stats = stats
        return records

    # 【函数框架 Repository.select】根据当前任务排序候选，输出装得下的相关目录卡。
    # 输入：task 与 budget=8000 字符。 输出：选中记录、索引数量、字符数和粗略 token 估计。
    # 1. 提取任务词并建索引
    # 2. 按 score 降序、路径升序排序
    # 3. 只查看前八候选，剔除零分项与超预算项
    # 4. 返回精简证据
    def select(self, task, budget=8000):
        # 小例子：文件词命中 1、符号命中 1、正文命中 2，得分 5+4+2=11。
        # 留意：排名只是线索，不证明候选一定该修改；输出并非完整文件正文。
        # 查询与源码使用同一提词规则；仍是可解释的字面匹配，不自动理解同义词或中英翻译。
        query = terms(task)
        records = self.index()

        # 【函数框架 Repository.select.score】给一张目录卡算可解释的相关分数。
        # 输入：单个 record 与外层 query 词集合。 输出：整数 score。
        # 1. 收集符号名
        # 2. 计算路径/符号/正文交集大小
        # 3. 按 5/4/1 权重相加
        def score(record):
            # 小例子：任务含 add，名为 add 的符号会贡献 4 分。
            # 留意：闭包记住 query；权重是人工启发式，不是训练出来的相关性模型。
            # 推导式把循环写紧凑：从 record['symbols'] 逐项取出 s，收集成迭代器。
            names = ' '.join((s['name'] for s in record['symbols']))
            # & 在这里用于集合交集，只留下两边都存在的成员。
            return 5 * len(query & terms(record['path'])) + 4 * len(query & terms(names)) + len(query & record['terms'])
        # 高分优先，同分按路径稳定排序：只查看前8候选，移除索引内部 terms；零分或超预算则跳过。
        # lambda 在这里定义一个小函数；函数被调用时才计算冒号后面的表达式。
        # 排序键取负数，会让原本较大的数排在前面；多个键组成元组时按先后比较。
        ranked = sorted(records, key=lambda item: (-score(item), item['path']))
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        selected, used = ([], 0)
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        for record in ranked[:8]:
            # 推导式把循环写紧凑：从 record.items() 逐项取出 (key, value)，仅保留满足 key != 'terms'
            # 的项，收集成字典。
            clean = {key: value for key, value in record.items() if key != 'terms'}
            clean['score'] = score(record)
            if not clean['score']:
                continue
            import json
            # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
            size = len(json.dumps(clean))
            if used + size > budget:
                continue
            selected.append(clean)
            used += size
        # 记录检索规模和材料代价：estimated_tokens=(used+3)//4 只是近似，与实际供应商 usage 分开。
        return {
            'indexed_files': len(records),
            'index_stats': dict(self.index_stats),
            'selected': selected,
            'characters': used,
            'estimated_tokens': (used + 3) // 4
        }

    # 【函数框架 Repository.symbols】直接查找函数或类的定义位置。
    # 输入：query。 输出：最多 60 个路径与符号记录。
    # 1. 读取索引
    # 2. 按名称大小写无关的子串匹配
    # 3. 返回 path、name、line、kind
    def symbols(self, query):
        # 小例子：find_symbols('add') 命中 app.py 的 add 定义。
        # 留意：它定位定义，不分析所有调用关系。
        results = []
        for record in self.index():
            for symbol in record['symbols']:
                if query.lower() in symbol['name'].lower():
                    # 字典里的 ** 把 symbol 的字段展开进来；后面的同名字段覆盖前面的字段。
                    results.append({'path': record['path'], **symbol})
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        return results[:60]
