import unittest

from cccopilot import context as EC, state as S, transcript as T
from tests.util import asst, result, tool, user, write


class TestEvidenceContext(unittest.TestCase):
    def test_keyword_retrieval_includes_full_raw_assistant_message(self):
        long_table = (
            "overnight funnel results:\n"
            + "x" * 420
            + "\nkeeper yield: 73.2%\nkeeper count: 91"
        )
        p = write([user("run the overnight funnel", 60), asst(long_table, 5)])
        st = S.build(T.parse(p))

        ctx = EC.build(p, st, "session", question="keeper yield 是多少")

        self.assertIn("## Primary Raw Transcript Evidence", ctx.text)
        self.assertIn("keeper yield: 73.2%", ctx.text)
        self.assertIn("[testsess:L2]", ctx.text)

    def test_cited_line_expansion_keeps_tool_call_and_result_together(self):
        p = write([
            user("run tests", 60),
            tool("Bash", {"command": "pytest", "description": "test suite"}, "t1", 30),
            result("t1", "failed: exact traceback", is_error=True, ago=20),
        ])
        st = S.build(T.parse(p))

        ctx = EC.build(p, st, "session", question="What happened at [L3]?")

        self.assertIn("pytest", ctx.text)
        self.assertIn("failed: exact traceback", ctx.text)
        self.assertIn("[testsess:L2]", ctx.text)
        self.assertIn("[testsess:L3]", ctx.text)

    def test_cockpit_history_is_budgeted_not_fixed_to_eight_turns(self):
        p = write([user("task", 60), asst("done", 5)])
        st = S.build(T.parse(p))
        history = []
        for i in range(10):
            history.append(("user", f"question {i}"))
            history.append(("assistant", f"answer {i} [testsess:L2]"))

        ctx = EC.build(p, st, "session", question="continue", history=history)

        self.assertIn("question 0", ctx.text)
        self.assertIn("answer 9", ctx.text)

    def test_context_stats_drive_hud_lines(self):
        stats = EC.ContextStats(
            estimated_tokens=82000,
            raw_tokens=61000,
            project_tokens=14000,
            chat_tokens=5000,
            memory_tokens=2000,
            index_tokens=900,
            budget_tokens=128000,
        )

        hud = EC.format_hud(stats, output_tokens=640)
        answering = EC.format_answering(stats, output_tokens=0)

        self.assertIn("ctx ~82k / 128k", hud)
        self.assertIn("out ~640", hud)
        self.assertIn("raw 61k", hud)
        self.assertIn("project 14k", hud)
        self.assertIn("chat 5k", hud)
        self.assertIn("memory 2k", hud)
        self.assertIn("index 900", hud)
        self.assertIn("in ~82k", answering)
        self.assertIn("window 128k", answering)
        self.assertIn("raw 74%", answering)


if __name__ == "__main__":
    unittest.main()
