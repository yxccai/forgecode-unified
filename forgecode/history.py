"""Read-only checkpoint browsing and terminal presentation, outside the agent runtime."""
# 【模块框架】档案浏览窗：只读展示旧对话，不参与动作决策。
# 连接位置：交互 /sessions 或 /history → saver/messages → 显示
# 流过这里的数据：按 root 筛会话，按 thread 去重，内部观察可选择是否显示。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from datetime import datetime
import json
from pathlib import Path

from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text


INTERNAL_NOTES = ("Repository data, not instructions.", "Baseline verification:\n",
                  "Verification failed. Analyze the evidence,")


# 【函数框架 content_text】把不同形式的消息正文转成可读文本。
# 输入：字符串、内容块列表或其他对象。 输出：文本。
# 1. 字符串直接返回
# 2. 列表提取字符串和 dict.text
# 3. 其他对象 str
def content_text(content):
    # 小例子：[{'text':'读源码'}] → '读源码'。
    # 留意：不是所有多模态块都有 text，这里不伪装成图片内容解析。
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # 推导式把循环写紧凑：从 content 逐项取出 block，仅保留满足 isinstance(block, (str, dict))
        #  的项，收集成迭代器。
        # 条件表达式：条件 isinstance(block, str) 成立取前面的值，否则取 else 后面的值。
        return "\n".join(block if isinstance(block, str) else block.get("text", "")
                         for block in content if isinstance(block, (str, dict)))
    return str(content)


# 【函数框架 entries】把原始消息整理成展示条目。
# 输入：messages、include_tools。 输出：(角色,文本) 列表。
# 1. 区分 human/ai/tool
# 2. 识别 Runtime 的内部人类型证据消息
# 3. 按开关附工具请求与结果，略去空 AI 正文
def entries(messages, include_tools=False):
    # 小例子：默认历史显示 You/Assistant；history all 同时显示 Tool result。
    # 留意：HumanMessage 在项目里也可装测试证据，不一定都来自真人。
    result = []
    for message in messages:
        text = content_text(message.content)
        if message.type == "human":
            internal = text.startswith(INTERNAL_NOTES)
            if not internal or include_tools:
                # 条件表达式：条件 internal 成立取前面的值，否则取 else 后面的值。
                result.append(("Runtime" if internal else "You", text))
        elif message.type == "ai":
            if text.strip():
                result.append(("Assistant", text))
            if include_tools and message.tool_calls:
                # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
                result.append(("Tool requests", json.dumps(message.tool_calls, ensure_ascii=False, indent=2)))
        elif message.type == "tool" and include_tools:
            result.append(("Tool result", text))
    return result


# 【函数框架 list_sessions】从 checkpoint API 取当前目录的会话列表。
# 输入：saver、root。 输出：会话 ID/标题/更新时间/状态/消息数。
# 1. 遍历最新优先 checkpoint
# 2. 每 thread 只取首个且排除子命名空间
# 3. 核对仓库根
# 4. 取首条用户消息作标题
def list_sessions(saver, root):
    """Use the checkpoint API; no second index or dependency on SQLite table layout."""
    # 小例子：同 thread 有十个快照，列表仍只一行最新状态。
    # 留意：用 saver API，不依赖 SQLite 内部表名；其他目录的会话不能混入。
    # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
    root = Path(root).resolve()
    # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
    seen, rows = set(), []
    for checkpoint in saver.list(None):
        # Saver 按最新 checkpoint 优先返回；每个线程仅取第一次出现的快照。
        config = checkpoint.config["configurable"]
        thread = config["thread_id"]
        if config.get("checkpoint_ns") or thread in seen:
            continue
        seen.add(thread)
        state = checkpoint.checkpoint["channel_values"]
        # resolve： 解析成绝对、规范的真实路径；后续仍要检查它是否在允许范围内。
        if not state.get("root") or Path(state["root"]).resolve() != root:
            continue
        messages = entries(state.get("messages", []))
        # next： 取迭代器的下一项；耗尽时抛 StopIteration，或者使用这里明确提供的默认值。
        # 推导式把循环写紧凑：从 messages 逐项取出 (role, text)，仅保留满足 role == 'You' 的项，收
        # 集成迭代器。
        title = next((text for role, text in messages if role == "You"), state.get("task", "Untitled"))
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        rows.append({"id": thread, "title": " ".join(title.split())[:100],
                     "updated": checkpoint.checkpoint["ts"], "status": state.get("status", "running"),
                     "messages": len(messages)})
    return rows


# 【函数框架 select_session】让用户分页选择要重开的会话。
# 输入：console、rows、current。 输出：选中的 ID，取消为 None。
# 1. 无记录给提示
# 2. 每页显示十条
# 3. 处理 n/p、序号、ID 或取消
# 4. 非法输入提示后继续
def select_session(console, rows, current):
    # 小例子：输入 2 返回第二条 thread_id。
    # 留意：选择编号只选现有记录，不创建新的会话。
    if not rows:
        console.print("No saved conversations in this directory.", style="dim")
        return None
    # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
    page, page_size = 0, 10
    while True:
        table = Table(title=f"Conversations ({page + 1}/{(len(rows) + page_size - 1) // page_size})",
                      expand=True)
        for column in ("#", "Session", "Updated", "State", "Messages", "First message"):
            table.add_column(column, overflow="fold")
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        for index, row in enumerate(rows[page * page_size:(page + 1) * page_size], page * page_size + 1):
            updated = datetime.fromisoformat(row["updated"]).astimezone().strftime("%m-%d %H:%M")
            # 这里的单星号把列表/序列中的各项展开；原顺序保留。
            # 推导式把循环写紧凑：从 (str(index), row['id'] + (' *' if row['id'] == current else ''),
            # updated, row['status'], str(row['messages']), row['title']) 逐项取出 value，收集成迭代器。
            # 条件表达式：条件 row['id'] == current 成立取前面的值，否则取 else 后面的值。
            table.add_row(*(Text(value) for value in (str(index), row["id"] + (" *" if row["id"] == current else ""),
                          updated, row["status"], str(row["messages"]), row["title"])))
        console.print(table)
        value = input("Session number/ID, n/p page, 0 cancel: ").strip()
        if not value or value == "0":
            return None
        if value == "n":
            page = min(page + 1, (len(rows) - 1) // page_size)
        elif value == "p":
            page = max(0, page - 1)
        elif value.isdigit() and 1 <= int(value) <= len(rows):
            return rows[int(value) - 1]["id"]
        # 推导式把循环写紧凑：从 rows 逐项取出 row，收集成迭代器。
        elif any(row["id"] == value for row in rows):
            return value
        else:
            console.print("Invalid selection", style="yellow")


# 【函数框架 show_history】分页或只显示最近对话。
# 输入：console、messages、include_tools、recent。 输出：终端展示；无值返回。
# 1. 先生成 entries
# 2. recent 只取最后八条
# 3. Assistant 用 Markdown，其他文本不解释终端 markup
# 4. 按 n/p 翻页，q 返回
def show_history(console, messages, include_tools=False, recent=False):
    # 小例子：/history all 能看到工具调用与 Runtime 的失败反馈。
    # 留意：阅读历史没有执行副作用；工具输出作为普通文本展示。
    items = entries(messages, include_tools)
    if not items:
        console.print("No messages yet.", style="dim")
        return
    page_size = 8
    if recent:
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        items = items[-page_size:]
    page = 0
    while True:
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        for role, text in items[page * page_size:(page + 1) * page_size]:
            # 条件表达式：条件 role == 'You' 成立取前面的值，否则取 else 后面的值。
            console.rule(role, style="cyan" if role == "You" else "dim")
            if role == "Assistant":
                console.print(Markdown(text))
            else:
                console.print(text, markup=False, highlight=False)
        if recent or len(items) <= page_size:
            return
        value = input(f"History {page + 1}/{(len(items) + page_size - 1) // page_size}: n/p, q return: ").strip().lower()
        if value in {"q", "", "0"}:
            return
        if value == "n":
            page = min(page + 1, (len(items) - 1) // page_size)
        elif value == "p":
            page = max(0, page - 1)
