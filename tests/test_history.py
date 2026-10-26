import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from rich.console import Console

from forgecode.config import Config
from forgecode.history import entries, list_sessions, select_session, show_history
from forgecode.interactive import main
from forgecode.session import Session
from forgecode.ui import Terminal
from tests.fixtures import fixture


class HistoryTest(unittest.TestCase):
    def test_database_listing_reopen_and_no_model_calls_when_browsing(self):
        class Model:
            def complete(self, *args):
                return AIMessage(content="Saved answer")
        with tempfile.TemporaryDirectory() as root:
            fixture(root)
            data = Path(root, ".forgecode")
            data.mkdir()
            db = str(data / "interactive.sqlite")
            with SqliteSaver.from_conn_string(db) as saver:
                for thread, task in (("older", "Remember amber"), ("newer", "Second topic")):
                    session = Session(root, thread, saver, Model(), lambda e: None)
                    session.turn(task, [], lambda r: False)
                session.turn("Follow up", [], lambda r: False)
                rows = list_sessions(saver, root)
                self.assertEqual([r["id"] for r in rows], ["newer", "older"])
                self.assertEqual(rows[0]["title"], "Second topic")
                self.assertEqual(rows[0]["messages"], 4)
                self.assertEqual(list_sessions(saver, Path(root, "other")), [])
            output = io.StringIO()
            ui = Terminal()
            ui.console = Console(file=output, width=100)
            with patch("sys.argv", ["forge", "--repo", root, "--sessions"]), \
                 patch("forgecode.interactive.load_config", return_value=Config()), \
                 patch("forgecode.interactive.Terminal", return_value=ui), \
                 patch("forgecode.interactive.Model") as model, \
                 patch("builtins.input", side_effect=["2", "/history", "/sessions", "1", "/status", "/exit"]):
                main()
            model.assert_not_called()
            self.assertIn("Remember amber", output.getvalue())
            self.assertIn("Saved answer", output.getvalue())
            self.assertIn("Session: newer", output.getvalue())

    def test_tool_detail_and_pagination(self):
        messages = [HumanMessage("User prompt"), HumanMessage("Baseline verification:\nfailed"),
                    AIMessage(content="", tool_calls=[{"id": "a", "name": "read_file", "args": {}}]),
                    ToolMessage(content="Source", tool_call_id="a"), AIMessage(content="Answer")]
        self.assertEqual(entries(messages), [("You", "User prompt"), ("Assistant", "Answer")])
        self.assertEqual(len(entries(messages, True)), 5)
        output = io.StringIO()
        console = Console(file=output)
        with patch("builtins.input", side_effect=["n", "p", "q"]):
            show_history(console, [HumanMessage(f"message {i}") for i in range(10)])
        self.assertIn("message 9", output.getvalue())
        with patch("builtins.input", side_effect=["n", "11"]):
            rows = [{"id": str(i), "title": "[red]literal", "updated": "2026-09-21T00:00:00+00:00",
                     "status": "answered", "messages": 2} for i in range(12)]
            self.assertEqual(select_session(console, rows, "0"), "10")
        self.assertIn("[red]literal", output.getvalue())
