import os
from pathlib import Path
import sys
import tempfile
from threading import Timer
import time
import unittest
from forgecode.execution.cancel import CancelToken
from forgecode.execution.process import command

class ExecutionTest(unittest.TestCase):
    def test_real_timeout_and_failure_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            result = command(root, [sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.2)
            self.assertTrue(result["timed_out"])
            result = command(root, [sys.executable, "-c", "import sys; print('FAIL_AT_END'); sys.exit(3)"])
            self.assertEqual(result["exit_code"], 3)
            self.assertIn("FAIL_AT_END", result["output"])

    def test_cancel_stops_process_tree(self):
        with tempfile.TemporaryDirectory() as root:
            cancel = CancelToken()
            script = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',\"import time; from pathlib import Path; time.sleep(1.2); Path('late-marker').write_text('late')\"]); time.sleep(20)"
            timer = Timer(0.4, cancel.cancel)
            timer.start()
            try:
                result = command(root, [sys.executable, "-c", script], cancel=cancel, timeout=5)
            finally:
                timer.cancel()
            self.assertTrue(result["cancelled"])
            time.sleep(1.3)
            self.assertFalse(Path(root, "late-marker").exists())
            if os.name == "nt":
                self.assertEqual(result["cleanup"], "windows_job")

    def test_background_child_is_closed_after_parent_exits(self):
        with tempfile.TemporaryDirectory() as root:
            script = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',\"import time; from pathlib import Path; time.sleep(0.7); Path('escaped-marker').write_text('late')\"])"
            result = command(root, [sys.executable, "-c", script])
            self.assertEqual(result["exit_code"], 0)
            time.sleep(0.8)
            self.assertFalse(Path(root, "escaped-marker").exists())
