"""事务提交后显式关闭 SQLite；Windows 上不能依赖垃圾回收解锁文件。"""
# 【模块框架】SQLite 事务小助手：完成后提交/回滚，再明确关闭。
# 连接位置：Memory/Receipts → with connect(path) as db → SQL
# 流过这里的数据：contextmanager + yield 暂时借出连接；with db 管事务，closing 管关闭。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from contextlib import closing, contextmanager
import sqlite3

# 【函数框架 connect】提供一段自动结束事务和关闭连接的作用域。
# 输入：数据库 path。 输出：供 with 使用的上下文管理器。
# 1. sqlite3.connect 打开连接
# 2. 进入事务作用域，yield 把 db 借给调用方
# 3. 正常返回提交，异常回滚，最终 closing 关闭
@contextmanager
def connect(path):
    # 小例子：with connect(self.db) as conn: conn.execute(...)。
    # 留意：with db 本身只管事务，不保证及时 close；两层 with 解决 Windows 文件锁。
    # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
    with closing(sqlite3.connect(path)) as db:
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with db:
            # yield 暂时交出一个值并保留执行位置；生成器下次继续，contextmanager 在 with 退出后继续收
            # 尾。
            yield db
