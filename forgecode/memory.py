"""经验带来源和验证依据；来源未变与结论正确分开表示。"""
# 【模块框架】经验柜：记经验时夹来源，借经验时核对新鲜度。
# 连接位置：remember 写 SQLite → recall 检索/过滤 → ContextBuilder.evidence → 模型输入
# 流过这里的数据：每条记忆含 note、kind、来源指纹、created、confidence、可选验证 evidence。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import json
from pathlib import Path
from forgecode.execution.storage import connect
import time
from forgecode.context.repository import terms
from forgecode.execution.paths import PathGuard
from forgecode.execution.receipts import fingerprint

# 【类框架 Memory】带来源的笔记库：保存内容指纹与依据 → 检索相关笔记 → 核对时效、置信值和
# 当前来源。
class Memory:
    # 【函数框架 Memory.__init__】打开经验库并保证表结构可用。
    # 输入：仓库 root、可选独立 store_root、emit。 输出：Memory 对象及 SQLite 表。
    # 1. 确定仓库与存储目录
    # 2. 创建 memories 表
    # 3. 查看列名，缺 evidence 列则补列
    def __init__(self, root, store_root=None, emit=lambda event: None):
        # 小例子：worktree 读的是当前源码，经验数据库可放在原样例仓库的 .forgecode。
        # 留意：只升级本记忆表的小字段，不会自动迁移旧版本 Agent checkpoint。
        # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
        self.root = Path(root).resolve()
        self.emit = emit
        folder = Path(store_root or root) / ".forgecode"
        # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
        folder.mkdir(exist_ok=True)
        self.db = folder / "memory.sqlite"
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with connect(self.db) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, kind TEXT, note TEXT, sources TEXT, created REAL, confidence REAL, evidence TEXT)")
            # 推导式把循环写紧凑：从 conn.execute('PRAGMA table_info(memories)') 逐项取出 row，收集成
            # 集合。
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
            if "evidence" not in columns:
                conn.execute("ALTER TABLE memories ADD COLUMN evidence TEXT")

    # 【函数框架 Memory.remember】存一条带来源指纹的经验。
    # 输入：note、1～10 个 sources、kind、confidence、可选 evidence。 输出：新记录的数据库 ID。
    # 1. 校验经验种类、文本长度与置信度
    # 2. 守卫来源路径并限制大小
    # 3. 记录每个来源的当前指纹
    # 4. 参数化 INSERT 写入时间与依据
    def remember(self, note, sources, kind="semantic", confidence=0.5, evidence=None):
        # 小例子：笔记 'add 在 app.py' + app.py 当前指纹；confidence=0.8。
        # 留意：source_validated 不在此时证明文本正确；普通 remember 调用不会自动变成已验证结论。
        # 经验合同先校验：semantic/episodic、非空长度、来源数量与置信值都有限制。
        if kind not in {"semantic", "episodic"} or not note.strip() or len(note) > 2000:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Invalid memory kind or note length")
        if not sources or len(sources) > 10 or not 0 <= confidence <= 1:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Memory requires 1-10 source paths and valid confidence")
        # 准备路径检查和出处记录：guard 检查引用文件在仓库范围内；fingerprints 是开始为空的字典，
        # 准备存每个来源的内容指纹。
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        guard, fingerprints = PathGuard(self.root), {}
        for name in sources:
            p = guard.path(name)
            if not p.is_file() or p.stat().st_size > 500000:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Invalid memory source")
            # 记下这个来源的版本：字典左边是相对文件名，右边是文件内容摘要。笔记随后和这些出处一起保存。
            fingerprints[p.relative_to(self.root).as_posix()] = fingerprint(p)
        # 写时间与可选验证证据：SQL 使用 ? 参数绑定。返回新记录ID；没有 evidence 时不能自行标成已
        # 验证。
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with connect(self.db) as conn:
            # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
            # 条件表达式：条件 evidence 成立取前面的值，否则取 else 后面的值。
            row = conn.execute("INSERT INTO memories(kind,note,sources,created,confidence,evidence) VALUES(?,?,?,?,?,?)",
                               (kind, note, json.dumps(fingerprints), time.time(), confidence, json.dumps(evidence) if evidence else None))
            return row.lastrowid

    # 【函数框架 Memory.recall】找相关经验，并解释为什么保留或淘汰。
    # 输入：query 与返回数量 limit。 输出：按相关性/新近排序的有效记忆。
    # 1. 读最近 500 条并还原 JSON
    # 2. 用任务词和笔记/路径交集算相关性
    # 3. 剔除超 30 天、低于 0.4、来源变更或失联的记录
    # 4. 发过滤原因；剩下的分别标记来源有效与验证依据
    # 5. 排序取前 limit
    def recall(self, query, limit=5):
        """先按相关性排序，再解释时效、置信度和文件指纹过滤原因。"""
        # 小例子：app.py 从 a-b 改为 a*b 后，旧来源指纹不符 → source_changed。
        # 留意：来源没变只说明出处仍匹配，不说明笔记绝对正确；confidence 是保存值，不是校准过的概
        # 率。
        # 先从柜子取候选：这里只读最近 500 条，避免无限扫描；外层 with 结束就关闭数据库。
        # query_terms 是当前任务的词集合。
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with connect(self.db) as conn:
            rows = conn.execute("SELECT id,kind,note,sources,created,confidence,evidence FROM memories ORDER BY id DESC LIMIT 500").fetchall()
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        query_terms, valid, guard = terms(query), [], PathGuard(self.root)
        # 把数据库列还原为有名字的笔记：zip 将列名与 row 值配对。sources/evidence 原来是 JSON 文本，
        # 现在还原为字典，便于核对。
        for row in rows:
            note = dict(zip(("id", "kind", "note", "sources", "created", "confidence", "evidence"), row))
            # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
            note["sources"] = json.loads(note["sources"])
            # 条件表达式：条件 note['evidence'] 成立取前面的值，否则取 else 后面的值。
            # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
            note["evidence"] = json.loads(note["evidence"]) if note["evidence"] else None
            # & 是集合交集：只留下任务词与笔记/出处词都出现的词，数量用作相关性分数。
            # & 在这里用于集合交集，只留下两边都存在的成员。
            note["score"] = len(query_terms & terms(note["note"] + " " + " ".join(note["sources"])))
            if not note["score"]:
                continue
            # 再检查时间、置信度和来源：超过 30 天、低于 0.4、文件变化或失联都给具体 reason。all 要所
            # 有来源指纹仍相符。
            reason = None
            # 先检查保鲜期与置信值：超过 30 天记 expired；保存的置信值低于 0.4 记 low_confidence。这是
            # 本项目的筛选规则。
            if time.time() - note["created"] > 30*86400:
                reason = "expired"
            elif note["confidence"] < 0.4:
                reason = "low_confidence"
            else:
                try:
                    # 现在的出处还相同吗：逐个比较当前内容指纹与存下的值。至少一个变了就记 source_changed，后
                    # 面跳过这条笔记。
                    # 推导式把循环写紧凑：从 note['sources'].items() 逐项取出 (name, value)，收集成迭代器。
                    if not all(fingerprint(guard.path(name)) == value for name, value in note["sources"].items()):
                        reason = "source_changed"
                except (ValueError, OSError):
                    reason = "source_unavailable"
            # 说明淘汰原因，区分两种标签：source_validated 表示来源仍匹配，verified 只看是否保存过
            # passed 依据。二者不能合并成‘笔记必然正确’。
            self.emit({"type": "memory_filter", "memory_id": note["id"], "reason": reason or "accepted"})
            if reason:
                continue
            # 两种依据分别标记：source_validated 说明出处仍匹配；verified 看保存的测试依据。两种标签不
            # 能相互替代。
            note.update(source_validated=True, verified=bool(note["evidence"] and note["evidence"].get("passed")))
            valid.append(note)
        # 负号把分数和创建时间倒过来排；所以高分优先，同分时新笔记优先，再取前 limit 条。
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        # lambda 在这里定义一个小函数；函数被调用时才计算冒号后面的表达式。
        # 排序键取负数，会让原本较大的数排在前面；多个键组成元组时按先后比较。
        return sorted(valid, key=lambda n: (-n["score"], -n["created"]))[:limit]
