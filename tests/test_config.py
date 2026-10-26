import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgecode.config import Config


class ConfigTest(unittest.TestCase):
    def test_windows_bridge_imports_only_requested_variables(self):
        from subprocess import CompletedProcess
        from forgecode.windows import windows_environment
        values = {"FORGECODE_API_KEY": "test-only", "FORGECODE_BASE_URL": "http://localhost/v1",
                  "FORGECODE_MODEL": "fixture", "UNRELATED": "ignored"}
        with patch.dict(os.environ, {}, clear=True), patch("forgecode.windows.subprocess.run",
                return_value=CompletedProcess([], 0, stdout=json.dumps(values).encode())):
            windows_environment()
            self.assertEqual(Config.load().model, "fixture")
            self.assertNotIn("UNRELATED", os.environ)

    def test_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder, "config.local.json")
            file.write_text(json.dumps({"model": "file", "api_key": "local"}))
            with patch.dict(os.environ, {"FORGECODE_MODEL": "forge", "OPENAI_MODEL": "openai"}, clear=True):
                self.assertEqual(Config.load(file).model, "forge")
                self.assertEqual(Config.load(file, model="cli").model, "cli")
                self.assertEqual(Config.load(file).api_key, "local")

    def test_bad_budget(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                Config.load(model="test", max_steps=0)
