import json
from pathlib import Path
import tempfile
import unittest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from forgecode.context.builder import ContextBuilder
from forgecode.tools.results import ToolResult, ResultStore

class ContextTest(unittest.TestCase):
    def test_current_goal_and_complete_latest_group_survive_compaction(self):
        with tempfile.TemporaryDirectory() as root:
            latest = ToolResult("read_file", "call", content="BEGIN "+ "x"*14000 + " END_ERROR").message()
            history = [SystemMessage("rules"), HumanMessage("old goal"+"z"*8000), AIMessage(content="old answer"),
                       HumanMessage("current goal"), AIMessage(content="", tool_calls=[{"name":"read_file","id":"call","args":{"path":"app.py"}}]),
                       ToolMessage(content=latest, tool_call_id="call")]
            state = {"messages": history, "task": "current goal", "plan": ""}
            builder = ContextBuilder(4500, ResultStore(root))
            request, stats = builder.build(state)
            self.assertIn("current goal", [m.content for m in request])
            self.assertFalse(any("old goal" in str(m.content) for m in request))
            self.assertEqual(request[-1].tool_call_id, "call")
            data = json.loads(request[-1].content)
            self.assertIn("END_ERROR", data["content"])
            self.assertTrue(data["source_ref"])
            self.assertEqual(history[-1].content, latest)
            self.assertLessEqual(stats["characters"], 4500)
            self.assertEqual(stats["compacted_tool_results"], 1)

    def test_incomplete_group_is_not_sent_to_endpoint(self):
        history = [HumanMessage("goal"), AIMessage(content="", tool_calls=[
            {"name":"list_files","args":{},"id":"a"},{"name":"list_files","args":{},"id":"b"}]),
            ToolMessage(content="done", tool_call_id="a")]
        request, _ = ContextBuilder().build({"messages":history,"task":"goal","plan":""})
        self.assertFalse(any(isinstance(m, ToolMessage) or (isinstance(m, AIMessage) and m.tool_calls) for m in request))

    def test_goal_exceeding_budget_is_not_silently_cut(self):
        with self.assertRaisesRegex(ValueError, "Current task"):
            ContextBuilder(1000).build({"messages":[HumanMessage("x"*2000)],"task":"x"*2000,"plan":""})
