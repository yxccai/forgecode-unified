"""checkpoint 之外的动作回执：记录执行开始、完成与未知结果。"""
# 【模块框架】动作签收单：与进度存档分开记录副作用是否开始、是否完成。
# 连接位置：tools_node.id_for → execute.get/begin → 副作用 → finish → 恢复核对
# 流过这里的数据：SQLite actions: id/status/expected/result/created；before/after 是文件内
# 容指纹。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import hashlib
import json
from pathlib import Path
from forgecode.execution.storage import connect
import time

# 【函数框架 fingerprint】给现有文件内容计算可比较的指纹。
# 输入：path。 输出：SHA256；文件不存在返回 None。
# 1. 检查存在性
# 2. 按字节读取
# 3. 计算摘要
def fingerprint(path):
    # 小例子：a-b 与 a+b 的 SHA256 不同。
    # 留意：摘要只能说明内容是否一致，不能证明业务正确。
    target = Path(path)
    # 条件表达式：条件 target.exists() 成立取前面的值，否则取 else 后面的值。
    # hexdigest： 把摘要转成十六进制文字，便于作为字典值或保存编号。
    # sha256： 把内容算成固定摘要；用来比对版本或参数，不是加密后可以再还原的文字。
    # read_bytes： 读取文件的原始字节；摘要计算以这些实际内容为依据。
    return hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None

# 【类框架 Receipts】动作回执库：动手前记预期，结束后记结果。恢复时先查事实，无法确认的命
# 令不能盲目重试。
class Receipts:
    # 【函数框架 Receipts.__init__】准备动作回执数据库。
    # 输入：root。 输出：Receipts 对象和 actions 表。
    # 1. 创建 .forgecode
    # 2. 确定 actions.sqlite
    # 3. 创建以动作 ID 为主键的表
    def __init__(self, root):
        # 小例子：同一工作区 .forgecode/actions.sqlite 保存动作签收。
        # 留意：回执库与 checkpoint 库不是一个原子事务，所以恢复必须核对现实。
        folder = Path(root) / ".forgecode"
        # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
        folder.mkdir(exist_ok=True)
        self.db = folder / "actions.sqlite"
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with connect(self.db) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, status TEXT, expected TEXT, result TEXT, created REAL)")

    # 【函数框架 Receipts.get】查某张动作签收单。
    # 输入：operation_id。 输出：解码的回执字典或 None。
    # 1. 参数化 SELECT
    # 2. 没有记录就返回 None
    # 3. 还原 expected/result JSON
    def get(self, operation_id):
        # 小例子：断电后看到 started，执行器再比文件 before/after。
        # 留意：started 且 result=None 表示执行结果待核对，不等于没执行。
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with connect(self.db) as conn:
            # SQL 的 ? 是参数占位符；(operation_id,) 是只有一项的元组，末尾逗号不能省。数据库按它查询
            # 这张单，fetchone 取一行或返回 None；不是把编号拼进 SQL 字符串执行。
            row = conn.execute("SELECT status,expected,result FROM actions WHERE id=?", (operation_id,)).fetchone()
        if not row:
            return None
        # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
        # 条件表达式：条件 row[2] 成立取前面的值，否则取 else 后面的值。
        return {
            "status": row[0],
            "expected": json.loads(row[1]),
            "result": json.loads(row[2]) if row[2] else None
        }

    # 【函数框架 Receipts.begin】动手前先记下该动作和预期。
    # 输入：operation_id 与 expected。 输出：插入 started 回执。
    # 1. 打开事务
    # 2. INSERT 主键和预期 JSON
    # 3. 事务提交后才返回
    def begin(self, operation_id, expected):
        # 小例子：编辑前记录 name、args_digest、before、after。
        # 留意：同 ID 已存在会报错；这套实现针对单所有者恢复，不支持并发抢同一动作。
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with connect(self.db) as conn:
            # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
            conn.execute("INSERT INTO actions VALUES(?,?,?,?,?)", (operation_id, "started", json.dumps(expected), None, time.time()))

    # 【函数框架 Receipts.finish】把真实结果填到签收单上。
    # 输入：operation_id 与 ToolResult。 输出：更新 status/result。
    # 1. asdict 转结果字段
    # 2. 参数化 UPDATE
    # 3. 提交事务
    def finish(self, operation_id, result):
        # 小例子：edit_file 返回成功后记录完整 ok 信封。
        # 留意：必须保存真实结果；模型宣称完成不能代替 finish。
        from dataclasses import asdict
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with connect(self.db) as conn:
            # 这里按 operation_id 找原来的开始记录，用参数化 UPDATE 写入真实 status 与 JSON 结果；外层
            #  with 正常退出后提交。SQLite 记录与图的 checkpoint 分别保存，恢复才需要交叉核对。
            conn.execute("UPDATE actions SET status=?,result=? WHERE id=?", (result.status, json.dumps(asdict(result), ensure_ascii=False), operation_id))

    # 【函数框架 Receipts.id_for】让恢复重入时仍认出同一张动作单。
    # 输入：run_id、step、cursor、call。 输出：稳定 SHA256 动作 ID。
    # 1. 按稳定顺序 JSON 化四项身份
    # 2. 编码并摘要
    # 3. 相同输入得到相同 ID
    def id_for(self, run_id, step, cursor, call):
        # 小例子：审批暂停前后 step/cursor/call 不变，ID 不变。
        # 留意：新 run_id 或不同参数意味着不同动作；这个 ID 不保证所有外部系统都支持恰好一次。
        # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
        raw = json.dumps([run_id, step, cursor, call], sort_keys=True, ensure_ascii=False)
        # hexdigest： 把摘要转成十六进制文字，便于作为字典值或保存编号。
        # sha256： 把内容算成固定摘要；用来比对版本或参数，不是加密后可以再还原的文字。
        return hashlib.sha256(raw.encode()).hexdigest()
