"""观察日志只展示发生的事实；恢复使用 checkpoint 与动作回执。"""
# 【模块框架】录像员：记录可核对的输入、动作和结果，并遮住已知秘密。
# 连接位置：Runtime/能力 emit → Trace → JSONL + Terminal/教材
# 流过这里的数据：每行一个事件，带 time/thread_id/run_id/step；恢复依赖 checkpoint 和回执。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import json
from pathlib import Path
import re
import time

# 【函数框架 redact】递归遮盖敏感字段、已知密钥和 Bearer 文本。
# 输入：任意 JSON 风格 value 与 secrets。 输出：清理后的数据。
# 1. 字典按字段名遮盖并递归
# 2. 列表/元组逐项递归
# 3. 字符串替换已知 secrets，再遮 Bearer
# 4. 其他值原样返回
def redact(value, secrets=()):
    # 小例子：{'api_key':'abc'} → {'api_key':'[redacted]'}。
    # 留意：这是已知模式脱敏，不保证识别所有未知秘密；不要把任何日志宣称绝对无敏感信息。
    if isinstance(value, dict):
        # 推导式把循环写紧凑：从 value.items() 逐项取出 (k, v)，收集成字典。
        # 条件表达式：条件 k.lower() in {'api_key', 'authorization', 'password', 'secret', '
        # access_token'} 成立取前面的值，否则取 else 后面的值。
        return {k: "[redacted]" if k.lower() in {"api_key", "authorization", "password", "secret", "access_token"} else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        # 推导式把循环写紧凑：从 value 逐项取出 v，收集成列表。
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                # replace： 得到替换后的新文字；单独调用文字 replace 还没有把结果写回磁盘。
                value = value.replace(secret, "[redacted]")
        value = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9_./+-]+", r"\1[redacted]", value)
    return value

# 【类框架 Trace】实际事件记录器：补会话身份与时间 → 脱敏 → 写 JSONL，供回放和排错。
class Trace:
    # 【函数框架 Trace.__init__】准备日志路径、界面接收器和任务关联范围。
    # 输入：path、sink、secrets、thread_id。 输出：Trace 可调用对象。
    # 1. 保存参数
    # 2. 初始化 scope
    # 3. 创建日志父目录
    def __init__(self, path, sink=lambda event: None, secrets=(), thread_id=None):
        # 小例子：sink=events.append 同时把真实事件交给教材。
        # 留意：secrets 可以是函数，交互配置改变后可读取最新密钥。
        # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
        self.path, self.sink, self.secrets = Path(path), sink, secrets
        self.thread_id = thread_id
        self.scope = {}
        # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # 【函数框架 Trace.__call__】补充关联字段、脱敏、写一行日志并同步展示。
    # 输入：event 字典。 输出：追加 JSONL，调用 sink；无值返回。
    # 1. 取得当前秘密列表
    # 2. 显式 run_id/step 更新 scope
    # 3. 加入时间和 thread 后递归脱敏
    # 4. 追加一行 JSON 并传给 sink
    def __call__(self, event):
        # 小例子：工具开始/结束可按 call_id 和 run_id 配对查看。
        # 留意：内部 memory_filter 没显式 run_id 时继承当前范围；scope 不是恢复状态存档。
        # 条件表达式：条件 callable(self.secrets) 成立取前面的值，否则取 else 后面的值。
        secrets = self.secrets() if callable(self.secrets) else self.secrets
        # 能力内部的过滤事件沿用当前节点的关联信息；新轮次的显式 run_id 会覆盖它。
        # 推导式把循环写紧凑：从 ('run_id', 'step') 逐项取出 key，仅保留满足 key in event 的项，收
        # 集成字典。
        self.scope.update({key: event[key] for key in ("run_id", "step") if key in event})
        # 补上身份，去掉敏感内容：time 和 thread_id 让事件可关联到会话；redact 去掉需保护的内容，
        # 再准备写入记录。
        # 字典里的 ** 把 self.scope、event 的字段展开进来；后面的同名字段覆盖前面的字段。
        clean = redact({"time": time.time(), "thread_id": self.thread_id, **self.scope, **event}, secrets)
        # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
        with self.path.open("a", encoding="utf-8") as stream:
            # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
            stream.write(json.dumps(clean, ensure_ascii=False) + "\n")
        self.sink(clean)
