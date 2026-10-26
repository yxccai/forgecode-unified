"""真实与脚本模型共用 complete；模型提出动作，工具执行由 Runtime 负责。"""
# 【模块框架】小脑的电话线：把消息送往模型端点，合并回来的一份回复。
# 连接位置：Runtime.plan/act → complete → 服务回复 → AIMessage → Runtime
# 流过这里的数据：真实 Model 与 ScriptModel 共用 complete(messages, tools, emit)；工具调用
# 由回复携带。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
from langchain_core.messages import AIMessage
from forgecode.execution.cancel import CancelToken
from langchain_openai import ChatOpenAI
from openai import DefaultHttpxClient

# 【类框架 Model】真实模型服务的适配器：装配客户端 → 发送消息与工具菜单 → 合并回复 → 交回
# AIMessage。
class Model:
    # 【函数框架 Model.close】使用完后释放该模型的网络连接。
    # 输入：当前 Model 实例。 输出：关闭 root_client；无值返回。
    # 1. 取得底层 root_client
    # 2. 存在才 close
    # 3. 由入口 finally 保证收尾
    def close(self):
        # 小例子：batch.main 的 finally 即使任务失败也调用 close。
        # 留意：不要靠垃圾回收及时关闭网络资源。
        # getattr： 读取可能存在的属性；第三个参数是属性不存在时的默认值。
        client = getattr(self.client, "root_client", None)
        # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
        if client is not None:
            client.close()

    # 【函数框架 Model.__init__】准备连接参数和本模型独占的 HTTP 客户端。
    # 输入：Config 与可选 CancelToken。 输出：Model 对象及 ChatOpenAI 客户端。
    # 1. 保存配置和取消信号
    # 2. 设置模型、端点、超时、一次 SDK 重试与独立客户端
    # 3. 按端点能力配置流式用量和推理参数
    def __init__(self, config, cancel=None):
        # 小例子：supports_usage=False 时不请求流式 usage，后续统计允许未知。
        # 留意：构造对象不等于发请求；独立客户端避免关闭本模型时影响另一个模型实例。
        self.config = config
        self.cancel = cancel or CancelToken()
        kwargs = dict(model=config.model, api_key=config.api_key or "unused", base_url=config.base_url,
                      timeout=config.timeout, max_retries=1,
                      http_client=DefaultHttpxClient(timeout=config.timeout))
        kwargs["stream_usage"] = config.supports_usage and config.streaming
        # 新版 SDK 的默认 keepalive transport 会改变代理发现；保留 httpx 的默认行为。
        if "http_socket_options" in ChatOpenAI.model_fields:
            kwargs["http_socket_options"] = ()
        if config.reasoning_effort:
            kwargs["reasoning_effort"] = config.reasoning_effort
        # 调用时的 ** 把字典按参数名展开；字段 key 的值相当于用 key=值 传给函数。
        self.client = ChatOpenAI(**kwargs)

    # 【函数框架 Model.complete】完成一次模型请求，流式碎片合成完整 AIMessage。
    # 输入：本次消息、工具菜单、emit。 输出：一份完整回复，可能含文本、tool_calls、usage。
    # 1. 检查取消与工具能力
    # 2. 有菜单时 bind_tools
    # 3. 非流式 invoke，或逐片 stream 合并并检查取消
    # 4. 关闭流、转换完整消息、记录 usage
    # 5. finally 发 model_end
    def complete(self, messages, tools, emit):
        # 小例子：两个碎片分别带 '{"pa' 和 'th":"app.py"}'，合成后才可解析参数。
        # 留意：total + chunk 是 SDK 的消息合并，能拼接被切开的工具 JSON 参数；不能简单拼正文代替
        # 它。
        # 拨号前检查服务能力：传工具菜单而端点不支持时直接报错；这样不让后面的错误回复被误当正常动
        # 作单。
        self.cancel.check()
        if tools and not self.config.supports_tools:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise ValueError("Configured endpoint does not support tools")
        emit({"type": "model_start", "model": self.config.model, "scripted": False})
        # 工具菜单绑定在请求客户端：bind_tools 提供可调用定义，不执行函数。无菜单时用原客户端，计
        # 划请求就是这种情况。
        # 条件表达式：条件 tools 成立取前面的值，否则取 else 后面的值。
        client = self.client.bind_tools(tools) if tools else self.client
        # try 中发生匹配的异常会走 except；finally 无论成功、异常或取消都执行，用于收尾。
        try:
            # 整份收或逐片收：invoke 一次拿完整回复；stream 按片接收。total + chunk 使用消息合并语义，
            # 既合文本也合 tool_call_chunks。
            if not self.config.streaming:
                # invoke： 这里才调用对象提供的执行接口；具体是模型、工具还是图，要看点号左侧的对象。
                answer = client.invoke(messages)
            else:
                # stream： 逐片接收模型回复；每片不一定是完整文字或完整工具参数。
                # 逗号是拆包：右边返回的各项按顺序分别交给左边的名字。
                stream, total = client.stream(messages), None
                # try 中发生匹配的异常会走 except；finally 无论成功、异常或取消都执行，用于收尾。
                try:
                    for chunk in stream:
                        self.cancel.check()
                        # 首片直接作为起点；后续 total + chunk 使用 SDK 的碎片合并，连工具参数碎片也一起合并。
                        # 条件表达式：条件 total is None 成立取前面的值，否则取 else 后面的值。
                        # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
                        total = chunk if total is None else total + chunk
                        if isinstance(chunk.content, str) and chunk.content:
                            emit({"type": "token", "text": chunk.content})
                # 流一定要关闭：即使取消或网络异常，内层 finally 尝试 close。空流明确报错，合并成功才
                # message_chunk_to_message。
                finally:
                    # hasattr： 确认属性是否存在；常用来适配不同库对象是否支持 close 等方法。
                    if hasattr(stream, "close"):
                        stream.close()
                # 一片都没收到属于异常；不能把空流伪装成模型完成了任务。
                # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
                if total is None:
                    # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                    raise RuntimeError("Empty model stream")
                from langchain_core.messages import message_chunk_to_message
                # message_chunk_to_message： 把累计的回复碎片转换成完整消息对象，后续再校验工具单。
                answer = message_chunk_to_message(total)
            # 记录用量再返回：回复无 usage 时 known=False。外层 finally 无论成功失败都发 model_end，让
            # 屏幕知道结束。
            self.cancel.check()
            # 条件表达式：条件 answer.usage_metadata 成立取前面的值，否则取 else 后面的值。
            # 字典里的 ** 把 answer.usage_metadata 的字段展开进来；后面的同名字段覆盖前面的字段。
            emit({"type": "usage", **answer.usage_metadata, "known": True}) if answer.usage_metadata else emit({"type": "usage", "known": False})
            return answer
        finally:
            emit({"type": "model_end"})

# 【类框架 ScriptModel】预设回复的模型适配器；用于学习执行机制。实际文件工具仍执行，预设回
# 复不证明真实模型效果。
class ScriptModel:
    """用于机制学习与测试；固定输出不证明真实模型的任务能力。"""
    # 【函数框架 ScriptModel.__init__】准备固定回复队列和请求记录。
    # 输入：answers 可迭代回复序列。 输出：ScriptModel。
    # 1. iter 把序列变成迭代器
    # 2. seen 初始化为空
    # 3. 等待正式 Runtime 来调用
    def __init__(self, answers):
        # 小例子：先计划，再读取，再错误修改，再回修，总共六次固定回复。
        # 留意：固定输出只帮助重复学习机制，不能拿来评估真实模型的智能。
        # iter： 取得可以逐项读取的迭代器，next 每次推进一项。
        self.answers = iter(answers)
        self.seen = []

    # 【函数框架 ScriptModel.complete】用固定下一条回复替代外部模型，其他流程保持真实。
    # 输入：messages、tools、emit。 输出：下一份 AIMessage。
    # 1. 记录输入并发开始事件
    # 2. next 取下一条；用完则明确报错
    # 3. 字符串包装成 AIMessage，记录用量与结束事件
    def complete(self, messages, tools, emit):
        # 小例子：回复 edit_file 动作后，正式工具仍要经历校验、审批和真实文件修改。
        # 留意：脚本用完意味着实际流程与预设不一致，不能无限重复最后一句来掩盖问题。
        self.seen.append(messages)
        emit({"type": "model_start", "model": "scripted", "scripted": True})
        try:
            # next： 取迭代器的下一项；耗尽时抛 StopIteration，或者使用这里明确提供的默认值。
            reply = next(self.answers)
        except StopIteration as exc:
            # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
            raise RuntimeError("Script exhausted; actual routing differs from lesson") from exc
        if isinstance(reply, str):
            reply = AIMessage(content=reply)
        # 字典里的 ** 把 reply.usage_metadata or {} 的字段展开进来；后面的同名字段覆盖前面的字段。
        emit({"type": "usage", **(reply.usage_metadata or {}), "known": bool(reply.usage_metadata), "scripted": True})
        emit({"type": "model_end"})
        return reply
