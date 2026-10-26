import json
from pathlib import Path
import tempfile
import unittest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from forgecode.agent.runtime import Runtime
from forgecode.agent.state import initial
from forgecode.execution.cancel import CancelToken
from forgecode.model import ScriptModel
from forgecode.tools.registry import ToolRegistry
from forgecode.fixtures import fixture
from forgecode.trace import Trace

class RuntimeTest(unittest.TestCase):
    def test_failed_or_invalid_model_requests_keep_accounting(self):
        class Failure:
            def complete(self, messages, tools, emit):
                raise RuntimeError("request failed after starting")
        for planning in (False, True):
            with self.subTest(planning=planning), tempfile.TemporaryDirectory() as root:
                events = []
                with SqliteSaver.from_conn_string(":memory:") as saver:
                    result = Runtime(Failure(), ToolRegistry(root, memory=False), events.append,
                                     planning=planning).build(saver).invoke(
                        initial("goal", root), {"configurable": {"thread_id": "failed"}})
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["steps"], 1)
                self.assertEqual(result["usage_missing"], 1)
                self.assertIsNone(next(e for e in events if e["type"] == "finish")["tokens"])
        with tempfile.TemporaryDirectory() as root:
            invalid_plan = AIMessage(content="", tool_calls=[{"name":"list_files","args":{},"id":"invalid-plan"}],
                                     usage_metadata={"input_tokens":3,"output_tokens":4,"total_tokens":7})
            with SqliteSaver.from_conn_string(":memory:") as saver:
                result = Runtime(ScriptModel([invalid_plan]), ToolRegistry(root, memory=False)).build(saver).invoke(
                    initial("goal", root), {"configurable": {"thread_id": "invalid"}})
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["steps"], 1)
            self.assertEqual(result["tokens"], 7)
            self.assertEqual(result["usage_missing"], 0)

    def test_memory_is_rechecked_after_source_changes(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "app.py").write_text("old", encoding="utf-8")
            tools = ToolRegistry(root)
            tools.memory.remember("app.py uses old", ["app.py"], confidence=0.8)
            reply = AIMessage(content="", tool_calls=[{"name": "edit_file",
                              "args": {"path": "app.py", "old": "old", "new": "new"}, "id": "edit"}])
            model = ScriptModel([reply, "done"])
            with SqliteSaver.from_conn_string(":memory:") as saver:
                result = Runtime(model, tools, planning=False).build(saver).invoke(
                    initial("app.py", root), {"configurable": {"thread_id": "fresh"}})
            self.assertTrue(result["evidence"]["repository"]["selected"])
            self.assertEqual(result["evidence"]["memory"], [])
            self.assertIn("app.py uses old", str(model.seen[0]))
            self.assertNotIn("app.py uses old", str(model.seen[1]))

    def test_plan_receives_repository_and_baseline_facts(self):
        with tempfile.TemporaryDirectory() as root:
            fixture(root)
            model = ScriptModel(["inspect add", "done"])
            events = []
            runtime = Runtime(model, ToolRegistry(root, memory=False), events.append)
            with SqliteSaver.from_conn_string(":memory:") as saver:
                graph = runtime.build(saver)
                graph.invoke(initial("fix add", root, ["python", "-m", "unittest", "discover"], max_repairs=0), {"configurable":{"thread_id":"plan"}})
            first = "\n".join(str(m.content) for m in model.seen[0])
            self.assertIn("app.py", first)
            self.assertIn("Baseline verification", first)
            self.assertTrue(any(e["type"]=="feedback" for e in events) is False)

    def test_unknown_usage_is_not_zero_cost(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            runtime = Runtime(ScriptModel(["answer"]), ToolRegistry(root, memory=False), events.append, planning=False)
            with SqliteSaver.from_conn_string(":memory:") as saver:
                result = runtime.build(saver).invoke(initial("goal", root), {"configurable":{"thread_id":"usage"}})
            self.assertEqual(result["usage_missing"], 1)
            self.assertIsNone(next(e for e in events if e["type"]=="finish")["tokens"])

    def test_cancel_prevents_next_model_and_tool_calls(self):
        with tempfile.TemporaryDirectory() as root:
            model = ScriptModel(["must not be consumed"])
            tools = ToolRegistry(root, memory=False)
            tools.cancel.cancel()
            with SqliteSaver.from_conn_string(":memory:") as saver:
                result = Runtime(model, tools, planning=False).build(saver).invoke(initial("goal", root), {"configurable":{"thread_id":"cancel"}})
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(model.seen, [])
            self.assertEqual(result["calls"], 0)

    def test_dangling_calls_are_closed_when_budget_stops(self):
        with tempfile.TemporaryDirectory() as root:
            reply = AIMessage(content="", tool_calls=[{"name":"list_files","args":{},"id":str(i)} for i in range(3)])
            with SqliteSaver.from_conn_string(":memory:") as saver:
                result = Runtime(ScriptModel([reply]), ToolRegistry(root, memory=False), planning=False).build(saver).invoke(
                    initial("goal",root,max_calls=1), {"configurable":{"thread_id":"budget"}})
            outputs = [m for m in result["messages"] if m.type=="tool"]
            self.assertEqual(len(outputs), 3)
            self.assertEqual(result["calls"], 1)
            self.assertEqual(sum(json.loads(m.content)["error_code"]=="not_executed" for m in outputs), 2)

    def test_trace_masks_secrets_in_file_and_view(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            trace = Trace(Path(root,"trace.jsonl"), events.append, secrets=("fixture-secret",))
            trace({"type":"request","api_key":"fixture-secret","text":"Bearer abc fixture-secret"})
            text = Path(root,"trace.jsonl").read_text()
            self.assertNotIn("fixture-secret", text)
            self.assertNotIn("Bearer abc", text)
            self.assertEqual(events[0]["api_key"], "[redacted]")
