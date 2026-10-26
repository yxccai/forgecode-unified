import tempfile
import unittest
from pathlib import Path
import io
import json
import os
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from forgecode.session import Session
from tests.fixtures import fixture


class InteractiveTest(unittest.TestCase):
    def test_unconfigured_start_and_settings_without_model_request(self):
        from forgecode.config import Config
        from forgecode.interactive import main
        from rich.console import Console
        from forgecode.ui import Terminal
        with tempfile.TemporaryDirectory() as root:
            ui = Terminal()
            output = io.StringIO()
            ui.console = Console(file=output)
            answers = iter(["/api", "1", "http://localhost:8000/v1", "3", "my-model",
                            "4", "6", "5", "/status", "/exit"])
            with patch("sys.argv", ["forge", "--repo", root]), \
                 patch("forgecode.interactive.load_config", return_value=Config()), \
                 patch("forgecode.interactive.Terminal", return_value=ui), \
                 patch("forgecode.interactive.Model") as model, \
                 patch("builtins.input", side_effect=lambda *a: next(answers)):
                main()
            model.assert_not_called()
            config = json.loads(Path(root, ".forgecode/config.local.json").read_text())
            self.assertEqual(config["model"], "my-model")
            self.assertEqual(config["reasoning_effort"], "high")
            self.assertEqual(config["base_url"], "http://localhost:8000/v1")

    def test_no_config_can_launch_and_profile_overrides_environment(self):
        from forgecode.interactive import load_config
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True), \
             patch("forgecode.windows.windows_environment", side_effect=OSError):
            with patch("forgecode.interactive.Path.exists", return_value=False):
                self.assertEqual(load_config().model, "")
            path = Path(root, "config.local.json")
            path.write_text('{"model":"saved","api_key":"private-fixture"}')
            with patch.dict(os.environ, {"FORGECODE_MODEL": "environment"}):
                self.assertEqual(load_config(str(path)).model, "saved")

    def test_followup_retains_history_after_reopen(self):
        seen = []
        class Model:
            def complete(self, messages, tools, emit):
                seen.append(messages)
                return AIMessage(content="Remembered")
        with tempfile.TemporaryDirectory() as root:
            fixture(root)
            db = str(Path(root, "session.sqlite"))
            for prompt in ("Remember my variable amber", "What variable did I mention?"):
                with SqliteSaver.from_conn_string(db) as saver:
                    session = Session(root, "test", saver, Model(), lambda e: None)
                    result = session.turn(prompt, [], lambda request: False)
                    self.assertEqual(result["status"], "answered")
            self.assertTrue(any("amber" in str(m.content) for m in seen[-1]))
            self.assertTrue(any(m.content == "Remembered" for m in seen[-1]))

    def test_edit_approval_and_resume(self):
        class Model:
            def complete(self, messages, tools, emit):
                if messages[-1].type == "tool":
                    return AIMessage(content="Done")
                return AIMessage(content="", tool_calls=[{"id": "edit", "name": "edit_file",
                    "args": {"path": "app.py", "old": "a - b", "new": "a + b"}}])
        with tempfile.TemporaryDirectory() as root:
            fixture(root)
            db = str(Path(root, "session.sqlite"))
            with SqliteSaver.from_conn_string(db) as saver:
                session = Session(root, "test", saver, Model(), lambda e: None)
                def cancel(request):
                    raise KeyboardInterrupt
                with self.assertRaises(KeyboardInterrupt):
                    session.turn("Fix", [], cancel)
                self.assertIn("a - b", Path(root, "app.py").read_text())
            with SqliteSaver.from_conn_string(db) as saver:
                session = Session(root, "test", saver, Model(), lambda e: None)
                session.turn("", [], lambda request: True, resume=True)
                self.assertIn("a + b", Path(root, "app.py").read_text())
