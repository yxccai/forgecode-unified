import json
from pathlib import Path
import sys
import tempfile
import unittest
from forgecode.execution.policy import Policy
from forgecode.execution.receipts import fingerprint
from forgecode.tools.registry import ToolRegistry

class Crash(BaseException):
    pass

class ToolContractTest(unittest.TestCase):
    def test_bad_args_unknown_tool_boundaries_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as root:
            registry = ToolRegistry(root, memory=False)
            Path(root, "app.py").write_text("same same", encoding="utf-8")
            self.assertEqual(registry.execute("read_file", {"path": "app.py", "extra": 1}).error_code, "invalid_args")
            self.assertEqual(registry.execute("missing", {}).error_code, "unknown_tool")
            self.assertIn("escapes", registry.execute("read_file", {"path": "../secret"}).content)
            self.assertIn("exactly once", registry.execute("edit_file", {"path": "app.py", "old": "same", "new": "other"}).content)
            self.assertEqual(Path(root, "app.py").read_text(), "same same")

    def test_approval_cannot_expand_to_another_action(self):
        with tempfile.TemporaryDirectory() as root:
            registry = ToolRegistry(root, memory=False, policy=Policy("interactive"))
            Path(root, "app.py").write_text("old", encoding="utf-8")
            args = {"path": "app.py", "old": "old", "new": "new"}
            request = registry.policy.request(root, "edit_file", args, "once", "write")
            grant = registry.policy.grant(request, True)
            self.assertEqual(registry.execute("edit_file", args, call_id="different", grant=grant).status, "denied")
            changed = {**args, "new": "expanded"}
            self.assertEqual(registry.execute("edit_file", changed, call_id="once", grant=grant).status, "denied")
            self.assertEqual(registry.execute("edit_file", args, call_id="once", grant=grant).status, "ok")
            self.assertEqual(Path(root, "app.py").read_text(), "new")

    def test_long_result_is_valid_json_and_can_be_read_back(self):
        with tempfile.TemporaryDirectory() as root:
            text = "head " + "x"*24000 + " IMPORTANT_ERROR_AT_END"
            Path(root, "app.py").write_text(text, encoding="utf-8")
            registry = ToolRegistry(root, memory=False)
            result = registry.execute("read_file", {"path": "app.py"}, call_id="long")
            envelope = json.loads(result.message())
            self.assertTrue(envelope["truncated"])
            self.assertIn("IMPORTANT_ERROR_AT_END", envelope["content"])
            restored = registry.results.read(result.source_ref, start=23000, length=3000)
            self.assertIn("IMPORTANT_ERROR_AT_END", restored["content"])
            self.assertEqual(registry.execute("read_result", {"result_id": "../.env"}).status, "error")

    def test_edit_effect_is_reconciled_after_crash(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "app.py").write_text("old", encoding="utf-8")
            def crash(*args):
                raise Crash()
            registry = ToolRegistry(root, memory=False, after_effect=crash)
            args = {"path": "app.py", "old": "old", "new": "new"}
            with self.assertRaises(Crash):
                registry.execute("edit_file", args, call_id="edit", operation_id="stable")
            self.assertEqual(Path(root, "app.py").read_text(), "new")
            recovered = ToolRegistry(root, memory=False).execute("edit_file", args, call_id="edit", operation_id="stable")
            self.assertTrue(recovered.content["recovered"])
            self.assertEqual(recovered.status, "ok")

    def test_unknown_command_effect_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as root:
            def crash(*args):
                raise Crash()
            registry = ToolRegistry(root, memory=False, after_effect=crash)
            args = {"argv": [sys.executable, "-c", "from pathlib import Path; Path('marker').write_text('one')"]}
            grant = registry.policy.grant(registry.policy.request(root, "run_command", args, "cmd", "command"), True)
            with self.assertRaises(Crash):
                registry.execute("run_command", args, call_id="cmd", grant=grant, operation_id="command")
            resumed = ToolRegistry(root, memory=False).execute("run_command", args, call_id="cmd", operation_id="command")
            self.assertEqual(resumed.status, "uncertain")
            self.assertEqual(Path(root, "marker").read_text(), "one")

    def test_resumed_edit_cannot_bypass_lowered_policy(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "app.py").write_text("old", encoding="utf-8")
            registry = ToolRegistry(root, memory=False)
            args = {"path": "app.py", "old": "old", "new": "new"}
            _, args = registry.prepare("edit_file", args)
            from forgecode.execution.policy import args_digest
            expected = {"name": "edit_file", "args_digest": args_digest(args),
                        "before": fingerprint(Path(root, "app.py")), "after": "different"}
            registry.receipts.begin("started", expected)
            lowered = ToolRegistry(root, memory=False, policy=Policy("readonly"))
            result = lowered.execute("edit_file", args, call_id="edit", operation_id="started")
            self.assertEqual(result.status, "denied")
            self.assertEqual(Path(root, "app.py").read_text(), "old")

    def test_verification_file_is_protected(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "test_public.py").write_text("assert False", encoding="utf-8")
            registry = ToolRegistry(root, memory=False, protected_paths=("test_public.py",))
            result = registry.execute("edit_file", {"path": "test_public.py", "old": "False", "new": "True"})
            self.assertEqual(result.status, "denied")
            disguised = registry.execute("edit_file", {"path": "./test_public.py", "old": "False", "new": "True"})
            self.assertEqual(disguised.status, "denied")
            if sys.platform == "win32":
                disguised = registry.execute("edit_file", {"path": "TEST_PUBLIC.PY", "old": "False", "new": "True"})
                self.assertEqual(disguised.status, "denied")
                Path(root, ".forgecode", "private").write_text("private", encoding="utf-8")
                self.assertEqual(registry.execute("read_file", {"path": ".FORGECODE/private"}).status, "error")
