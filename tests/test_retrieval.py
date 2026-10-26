from pathlib import Path
import tempfile
import unittest

from forgecode.memory import Memory
from forgecode.context.repository import Repository
from forgecode.tools.registry import ToolRegistry as Tools


class RepositoryTest(unittest.TestCase):
    def test_symbol_ranking_and_memory_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "invoice.py").write_text("def invoice_total(items):\n return sum(items)\n")
            Path(folder, "unrelated.py").write_text("def paint(color): pass\n")
            repo = Repository(folder)
            self.assertEqual(repo.select("invoice_total bug")["selected"][0]["path"], "invoice.py")
            self.assertEqual(repo.symbols("invoice")[0]["line"], 1)
            Memory(folder).remember("invoice total is in invoice.py", ["invoice.py"])
            recalled = Memory(folder).recall("invoice")
            self.assertEqual(len(recalled), 1)
            self.assertIn("invoice.py", recalled[0]["sources"])
            self.assertEqual(Memory(folder).recall("network"), [])

    def test_context_budget_and_secret_exclusion(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "config.local.json").write_text('{"key":"secret"}')
            for i in range(30):
                Path(folder, f"part{i}.py").write_text(f"def invoice_{i}(): pass\n")
            selected = Repository(folder).select("invoice", budget=500)
            self.assertLessEqual(selected["characters"], 500)
            self.assertEqual(selected["indexed_files"], 30)
            tools = Tools(folder)
            self.assertNotIn("config.local.json", tools.files.list_files())
            self.assertEqual("error", tools.execute("remember", {"note": "bad", "sources": ["../escape"]}).status)
