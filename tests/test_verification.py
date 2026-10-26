import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from forgecode.fixtures import fixture
from forgecode.agent.runtime import Runtime
from forgecode.agent.state import initial
from forgecode.memory import Memory
from forgecode.tools.registry import ToolRegistry as Tools
from forgecode.execution.workspace import Workspace, git


class ReliableTest(unittest.TestCase):
    def test_denied_command_never_executes(self):
        class Script:
            def complete(self, messages, tools, emit):
                if messages[-1].type == "tool":
                    return AIMessage(content="Denied, stopping")
                return AIMessage(content="", tool_calls=[{"name": "run_command", "id": "denied",
                    "args": {"argv": ["python3", "-c", "open('marker','w').write('x')"]}}])
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            with SqliteSaver.from_conn_string(":memory:") as saver:
                graph = Runtime(Script(), Tools(folder, memory=False), lambda e: None, False).build(saver)
                opts = {"configurable": {"thread_id": "deny"}}
                result = graph.invoke(initial("deny", folder, ["true"], verification=False), opts)
                self.assertTrue(result.get("__interrupt__"))
                result = graph.invoke(Command(resume=False), opts)
                self.assertEqual(result["status"], "answered")
                self.assertFalse(Path(folder, "marker").exists())

    def test_repair_approval_and_isolation(self):
        class Script:
            def __init__(self):
                self.responses = iter([
                    AIMessage(content="", tool_calls=[{"name": "edit_file", "args": {
                        "path": "app.py", "old": "a - b", "new": "a * b"}, "id": "bad"}]),
                    AIMessage(content="First attempt"),
                    AIMessage(content="", tool_calls=[{"name": "edit_file", "args": {
                        "path": "app.py", "old": "a * b", "new": "a + b"}, "id": "fix"}]),
                    AIMessage(content="", tool_calls=[{"name": "run_command", "args": {
                        "argv": ["python3", "-m", "unittest", "discover"]}, "id": "test"}]),
                    AIMessage(content="Done")])
            def complete(self, *args):
                return next(self.responses)
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            workspace = Workspace(folder, "repair")
            verify = ["python3", "-m", "unittest", "discover"]
            workspace.create(verify)
            events, model = [], Script()
            opts = {"configurable": {"thread_id": "repair"}}
            db = str(Path(folder, ".forgecode", "state.sqlite"))
            with SqliteSaver.from_conn_string(db) as saver:
                graph = Runtime(model, Tools(workspace.path), events.append, False).build(saver)
                result = graph.invoke(initial("fix add", str(workspace.path), verify), opts)
                self.assertTrue(result.get("__interrupt__"))
                self.assertEqual(result["repairs"], 1)
                self.assertFalse(any(e["type"] == "approval" for e in events))
            with SqliteSaver.from_conn_string(db) as saver:
                graph = Runtime(model, Tools(workspace.path), events.append, False).build(saver)
                result = graph.invoke(Command(resume=True), opts)
            self.assertEqual(result["status"], "verified")
            finals = [e["exit_code"] for e in events if e["type"] == "verification" and e["phase"] == "final"]
            self.assertEqual(finals, [1, 0])
            self.assertIn("a - b", Path(folder, "app.py").read_text())
            patch = workspace.export()
            git(folder, "apply", "--check", str(patch))
            self.assertIn("a + b", patch.read_text())

    def test_stale_memory_and_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            memory = Memory(folder)
            memory.remember("add function", ["app.py"])
            self.assertTrue(memory.recall("add"))
            Path(folder, "app.py").write_text("changed")
            self.assertEqual(memory.recall("add"), [])
            with self.assertRaisesRegex(ValueError, "Commit or stash"):
                Workspace(folder, "dirty").create(["true"])

    def test_export_new_file(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            workspace = Workspace(folder, "new")
            workspace.create(["true"])
            Path(workspace.path, "new file.txt").write_text("trailing spaces  \n\n")
            patch = workspace.export()
            git(folder, "apply", "--check", str(patch))
            self.assertIn("+trailing spaces  \n+\n", patch.read_text())
