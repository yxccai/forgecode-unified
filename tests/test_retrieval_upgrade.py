"""检索升级的反例：看查询能否命中，以及缓存是否会读到旧版本。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import forgecode.context.repository as module


class RetrievalUpgradeTest(unittest.TestCase):
    def test_camel_case_and_acronym_queries_keep_identifier_and_parts(self):
        self.assertTrue({'computeinvoicetotal', 'compute', 'invoice', 'total'} <= module.terms('computeInvoiceTotal'))
        self.assertTrue({'httpserver', 'http', 'server'} <= module.terms('HTTPServer'))
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'billing.py').write_text('def computeInvoiceTotal(items):\n return sum(items)\n', encoding='utf-8')
            self.assertEqual(module.Repository(folder).select('Invoice Total')['selected'][0]['path'], 'billing.py')

    def test_chinese_literal_overlap_works_without_claiming_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'billing.py').write_text('def total(items):\n """计算发票总额"""\n return sum(items)\n', encoding='utf-8')
            repo = module.Repository(folder)
            self.assertEqual(repo.select('修复发票总额')['selected'][0]['path'], 'billing.py')
            self.assertEqual(repo.select('网络连接超时')['selected'], [])

    def test_unchanged_files_are_parsed_once_and_results_match_a_cold_index(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('alpha', 'beta', 'gamma'):
                Path(folder, name + '.py').write_text(f'def {name}(): pass\n', encoding='utf-8')
            repo = module.Repository(folder)
            with patch.object(module.ast, 'parse', wraps=module.ast.parse) as parse:
                first = repo.select('alpha')
                second = repo.select('alpha')
                third = repo.symbols('alpha')
                self.assertEqual(parse.call_count, 3)
            self.assertEqual(second['selected'], first['selected'])
            self.assertEqual(second['selected'], module.Repository(folder).select('alpha')['selected'])
            self.assertEqual(third[0]['name'], 'alpha')
            self.assertEqual(second['index_stats']['cache_hits'], 3)
            self.assertEqual(second['index_stats']['parsed_python'], 0)

    def test_same_size_rewrite_with_preserved_mtime_cannot_reuse_old_symbols(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'subject.py')
            path.write_text('def alpha(): pass\n', encoding='utf-8')
            repo = module.Repository(folder)
            repo.index()
            stat = path.stat()
            path.write_text('def omega(): pass\n', encoding='utf-8')
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertEqual(path.stat().st_size, stat.st_size)
            with patch.object(module.ast, 'parse', wraps=module.ast.parse) as parse:
                self.assertEqual(repo.symbols('omega')[0]['name'], 'omega')
                self.assertEqual(parse.call_count, 1)
            self.assertEqual(repo.symbols('alpha'), [])

    def test_delete_rename_and_syntax_error_repair_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'old.py')
            path.write_text('def alpha(): pass\n', encoding='utf-8')
            repo = module.Repository(folder)
            repo.index()
            path.rename(Path(folder, 'new.py'))
            self.assertEqual([r['path'] for r in repo.index()], ['new.py'])
            path = Path(folder, 'new.py')
            path.write_text('def broken(\n', encoding='utf-8')
            self.assertEqual(repo.symbols('alpha'), [])
            path.write_text('def repaired(): pass\n', encoding='utf-8')
            self.assertEqual(repo.symbols('repaired')[0]['name'], 'repaired')
            path.unlink()
            self.assertEqual(repo.index(), [])
            self.assertEqual(repo.index_stats['cached_files'], 0)

    def test_returned_record_mutation_cannot_poison_cached_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'subject.py').write_text('def alpha(): pass\n', encoding='utf-8')
            repo = module.Repository(folder)
            record = repo.index()[0]
            record['symbols'][0]['name'] = 'forged'
            record['terms'].clear()
            self.assertEqual(repo.symbols('alpha')[0]['name'], 'alpha')
            self.assertNotEqual(repo.select('alpha')['selected'], [])

    def test_cache_source_byte_budget_and_no_cache_mode_preserve_correctness(self):
        with tempfile.TemporaryDirectory() as folder:
            source = 'def alpha(): pass\n'
            Path(folder, 'a.py').write_text(source, encoding='utf-8')
            Path(folder, 'b.py').write_text(source, encoding='utf-8')
            # Windows 文本写入可能把 \n 变为 \r\n；缓存口径是磁盘字节，不是字符串长度。
            repo = module.Repository(folder, cache_bytes=Path(folder, 'a.py').stat().st_size)
            first, second = repo.select('alpha'), repo.select('alpha')
            self.assertEqual(first['selected'], second['selected'])
            self.assertLessEqual(second['index_stats']['cached_source_bytes'], repo.cache_bytes)
            self.assertEqual(second['index_stats']['cache_hits'], 1)
            self.assertEqual(second['index_stats']['parsed_python'], 1)
            cold = module.Repository(folder, cache_bytes=0)
            self.assertEqual(cold.select('alpha')['selected'], second['selected'])
            self.assertEqual(cold.select('alpha')['index_stats']['cache_hits'], 0)
            with self.assertRaises(ValueError):
                module.Repository(folder, cache_bytes=-1)

    def test_zero_budget_does_not_cache_even_empty_files(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'empty.py').write_bytes(b'')
            repo = module.Repository(folder, cache_bytes=0)
            repo.index()
            repo.index()
            self.assertEqual(repo.index_stats['cache_hits'], 0)
            self.assertEqual(repo.index_stats['cached_files'], 0)
