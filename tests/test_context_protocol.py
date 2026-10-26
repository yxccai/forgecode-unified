"""请求与回单必须一一对应；比较集合相等还不够。"""
import unittest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from forgecode.context.builder import ContextBuilder, complete_groups


class ContextProtocolTest(unittest.TestCase):
    @staticmethod
    def history(ids, results):
        return [HumanMessage('goal'), AIMessage(content='', tool_calls=[
            {'name': 'list_files', 'args': {}, 'id': value} for value in ids
        ]), *[ToolMessage(content='result', tool_call_id=value) for value in results]]

    def test_duplicate_result_is_rejected_even_when_id_sets_match(self):
        history = self.history(['a', 'b'], ['a', 'a', 'b'])
        request, _ = ContextBuilder().build({'task': 'goal', 'messages': history, 'plan': ''})
        self.assertFalse(any(isinstance(m, ToolMessage) or (isinstance(m, AIMessage) and m.tool_calls) for m in request))
        self.assertEqual(len(history), 5)

    def test_duplicate_or_empty_request_ids_are_rejected(self):
        for ids, results in ((['a', 'a'], ['a', 'a']), ([''], [''])):
            with self.subTest(ids=ids):
                self.assertEqual(len(complete_groups(self.history(ids, results))), 1)

    def test_unknown_missing_or_excess_results_cannot_reach_provider(self):
        for results in (['a'], ['a', 'x'], ['a', 'b', 'x']):
            with self.subTest(results=results):
                self.assertEqual(len(complete_groups(self.history(['a', 'b'], results))), 1)

    def test_unique_complete_results_may_arrive_in_reverse_order(self):
        groups = complete_groups(self.history(['a', 'b'], ['b', 'a']))
        self.assertEqual(len(groups), 2)
        self.assertEqual([m.tool_call_id for m in groups[1][1:]], ['b', 'a'])
