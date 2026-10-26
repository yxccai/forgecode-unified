"""本地 HTTP 服务验证真实 SDK 的 SSE 合并，不连接外部模型。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import Mock
from langchain_core.messages import AIMessageChunk, HumanMessage
from forgecode.config import Config
from forgecode.execution.cancel import Cancelled, CancelToken
from forgecode.model import Model
from forgecode.tools.registry import ToolRegistry

class ModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                cls.requests.append(body)
                self.send_response(200)
                if not body.get("stream"):
                    payload = {"id":"local","object":"chat.completion","created":1,"model":"local",
                               "choices":[{"index":0,"message":{"role":"assistant","content":"answer"},"finish_reason":"stop"}],
                               "usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}
                    raw = json.dumps(payload).encode()
                    self.send_header("Content-Type","application/json")
                    self.send_header("Content-Length",str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                self.send_header("Content-Type","text/event-stream")
                self.end_headers()
                arguments = json.dumps({"path":"marker.py","old":"","new":"proposed only"})
                base = {"id":"local","object":"chat.completion.chunk","created":1,"model":"local"}
                chunks = [
                    {**base,"choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"edit","type":"function","function":{"name":"edit_file","arguments":arguments[:20]}}]},"finish_reason":None}]},
                    {**base,"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":arguments[20:]}}]},"finish_reason":None}]},
                    {**base,"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]},
                    {**base,"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}]
                for chunk in chunks:
                    self.wfile.write(("data: "+json.dumps(chunk)+"\n\n").encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
        cls.server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.thread = Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def config(self, **changes):
        return Config(model="local",base_url=f"http://127.0.0.1:{self.server.server_port}/v1",api_key="fixture",**changes)

    def test_real_sdk_merges_split_tool_arguments_without_executing(self):
        with tempfile.TemporaryDirectory() as root:
            tools = ToolRegistry(root,memory=False,repo_context=False)
            model = Model(self.config())
            events=[]
            try:
                answer=model.complete([HumanMessage("propose")],tools.items,events.append)
            finally:
                model.close()
            self.assertEqual(answer.tool_calls[0]["args"]["path"],"marker.py")
            self.assertEqual(answer.usage_metadata["total_tokens"],7)
            self.assertFalse(Path(root,"marker.py").exists())
            self.assertEqual(self.requests[-1]["stream_options"],{"include_usage":True})

    def test_non_streaming_endpoint_has_no_stream_usage_option(self):
        model=Model(self.config(streaming=False,supports_usage=False))
        try:
            answer=model.complete([HumanMessage("answer")],[],lambda e:None)
        finally:
            model.close()
        self.assertEqual(answer.content,"answer")
        self.assertNotIn("stream_options",self.requests[-1])

    def test_cancel_closes_stream_and_unsupported_tools_fail_before_network(self):
        cancel=CancelToken()
        model=Model(self.config(),cancel)
        class Stream:
            closed=False
            def __iter__(self):
                cancel.cancel()
                yield AIMessageChunk(content="blocked")
            def close(self):
                self.closed=True
        stream=Stream()
        original=model.client
        model.client=Mock()
        model.client.stream.return_value=stream
        try:
            with self.assertRaises(Cancelled):
                model.complete([HumanMessage("goal")],[],lambda e:None)
            self.assertTrue(stream.closed)
        finally:
            model.client=original
            model.close()
        blocked=Model(self.config(supports_tools=False))
        try:
            with self.assertRaisesRegex(ValueError,"does not support"):
                blocked.complete([HumanMessage("goal")],[object()],lambda e:None)
        finally:
            blocked.close()
