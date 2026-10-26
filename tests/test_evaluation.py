import unittest
from forgecode.evaluation.runner import self_check, summarize

class EvaluationTest(unittest.TestCase):
    def test_oracles_reject_baseline_accept_reference_including_cross_files(self):
        report = self_check()
        self.assertTrue(report["passed"])
        self.assertEqual(report["tasks"],22)
        self.assertEqual(sum(row["category"]=="cross_file_bug" for row in report["rows"]),2)

    def test_matched_pairs_do_not_turn_unknown_usage_into_zero(self):
        base={"task":"same","repeat":0,"variant":"full","success":True,"tests_passed":3,"tests_total":3,
              "steps":4,"tool_calls":2,"tokens":100,"latency_seconds":2,"failure":None}
        changed={**base,"variant":"no_memory","success":False,"tokens":None,"latency_seconds":3,"failure":"independent_acceptance"}
        report=summarize([base,changed])["no_memory"]
        self.assertIsNone(report["mean_tokens"])
        self.assertEqual(report["usage_unknown_runs"],1)
        self.assertEqual(report["paired"]["success_delta"],-1)
        self.assertIsNone(report["paired"]["token_delta"])
        self.assertEqual(report["paired"]["token_pair_count"],0)
