from pathlib import Path
import tempfile
import unittest

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from forgecode.tools.registry import ToolRegistry as Tools
from forgecode.agent.runtime import Runtime
from forgecode.agent.state import initial


class Model:
    def complete(self, messages, tools, emit):
        if not tools:
            return AIMessage(content="Inspect, edit, test")
        if messages[-1].type == "tool":
            return AIMessage(content="Finished")
        return AIMessage(content="", tool_calls=[
            {"name": "read_file", "args": {"path": "app.py"}, "id": "read"}])


class StateTest(unittest.TestCase):
    def test_batched_calls_respect_budget(self):
        class Batch:
            def complete(self, messages, tools, emit):
                return AIMessage(content="", tool_calls=[
                    {"name": "list_files", "args": {}, "id": str(i)} for i in range(3)])
        with tempfile.TemporaryDirectory() as folder:
            with SqliteSaver.from_conn_string(":memory:") as saver:
                graph = Runtime(Batch(), Tools(folder), lambda e: None, planning=False).build(saver)
                result = graph.invoke(initial("task", folder, max_calls=1),
                                      {"configurable": {"thread_id": "calls"}})
                self.assertEqual(result["status"], "budget_exhausted")
                self.assertEqual(result["calls"], 1)

    def test_reopen_database_and_resume_without_repeating_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "app.py").write_text("hello")
            db = str(Path(folder, "state.sqlite"))
            cfg = {"configurable": {"thread_id": "test"}}
            events = []
            with SqliteSaver.from_conn_string(db) as saver:
                graph = Runtime(Model(), Tools(folder), events.append).build(saver, True)
                graph.invoke(initial("inspect", folder), cfg)
                self.assertTrue(graph.get_state(cfg).next)
            with SqliteSaver.from_conn_string(db) as saver:
                graph = Runtime(Model(), Tools(folder), events.append).build(saver)
                result = graph.invoke(None, cfg)
                self.assertEqual(result["status"], "answered")
                self.assertEqual(result["calls"], 1)
                self.assertFalse(graph.get_state(cfg).next)
            self.assertEqual(sum(e["type"] == "tool_end" for e in events), 1)

    def test_plan_consumes_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            with SqliteSaver.from_conn_string(":memory:") as saver:
                graph = Runtime(Model(), Tools(folder), lambda e: None).build(saver)
                result = graph.invoke(initial("task", folder, max_steps=1),
                                      {"configurable": {"thread_id": "budget"}})
                self.assertEqual(result["status"], "budget_exhausted")
                self.assertEqual(result["calls"], 0)
