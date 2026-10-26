"""副作用已发生、图进度尚未提交时，重开数据库恢复同一个工具节点。"""
from pathlib import Path
import sys
import tempfile
import unittest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from forgecode.agent.runtime import Runtime
from forgecode.agent.state import initial
from forgecode.model import ScriptModel
from forgecode.tools.registry import ToolRegistry

class Crash(BaseException):
    pass

class RecoveryTest(unittest.TestCase):
    def recover(self, root, call, command=False):
        options = {"configurable": {"thread_id": "crash"}}
        database = str(Path(root, "checkpoint.sqlite"))
        def fail_after_effect(*args):
            raise Crash()
        registry = ToolRegistry(root, memory=False, after_effect=fail_after_effect)
        with SqliteSaver.from_conn_string(database) as saver:
            graph = Runtime(ScriptModel([AIMessage(content="", tool_calls=[call])]),
                            registry, planning=False).build(saver)
            if command:
                paused = graph.invoke(initial("task", root), options)
                self.assertTrue(paused["__interrupt__"])
            with self.assertRaises(Crash):
                graph.invoke(Command(resume=True) if command else initial("task", root), options)
            self.assertTrue(graph.get_state(options).next)
        resumed_model = ScriptModel(["done"])
        with SqliteSaver.from_conn_string(database) as saver:
            graph = Runtime(resumed_model, ToolRegistry(root, memory=False), planning=False).build(saver)
            result = graph.invoke(None, options)
        return result, resumed_model

    def test_edit_reconciles_after_reopening_checkpoint(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "app.py").write_text("old", encoding="utf-8")
            call = {"name": "edit_file", "args": {"path": "app.py", "old": "old", "new": "new"}, "id": "edit"}
            result, model = self.recover(root, call)
            self.assertEqual(result["status"], "answered")
            self.assertEqual(result["calls"], 1)
            self.assertEqual(Path(root, "app.py").read_text(), "new")
            self.assertEqual(len(model.seen), 1)

    def test_unknown_command_stops_after_reopening_checkpoint(self):
        with tempfile.TemporaryDirectory() as root:
            script = "from pathlib import Path; p=Path('marker'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"
            call = {"name": "run_command", "args": {"argv": [sys.executable, "-c", script]}, "id": "cmd"}
            result, model = self.recover(root, call, command=True)
            self.assertEqual(result["status"], "needs_review")
            self.assertEqual(Path(root, "marker").read_text(), "x")
            self.assertEqual(model.seen, [])
