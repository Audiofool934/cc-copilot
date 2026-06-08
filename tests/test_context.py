import os
import tempfile
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

    def test_memory_text_is_separate_budgeted_context_tier(self):
        p = write([user("task", 60), asst("done", 5)])
        st = S.build(T.parse(p))

        ctx = EC.build(p, st, "session", question="continue",
                       history=[("user", "recent q"), ("assistant", "recent a")],
                       memory_text="- Decisions made: keep project read-only.")

        self.assertIn("## Durable Cockpit Memory", ctx.text)
        self.assertIn("keep project read-only", ctx.text)
        self.assertGreater(ctx.stats.memory_tokens, 0)

    def test_project_context_retrieves_question_relevant_file_excerpt(self):
        cwd = tempfile.mkdtemp(prefix="ccctx-project-")
        os.makedirs(os.path.join(cwd, "src"))
        with open(os.path.join(cwd, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Demo Project\n\nOperator notes.\n")
        with open(os.path.join(cwd, "src", "metrics.txt"), "w", encoding="utf-8") as f:
            f.write("overnight funnel\nkeeper yield: 73.2%\nkeeper count: 91\n")
        with open(os.path.join(cwd, "src", "unrelated.txt"), "w", encoding="utf-8") as f:
            f.write("unrelated implementation detail that should not become an excerpt\n")
        p = write([user("inspect project", 60, sessionId="testsess", cwd=cwd),
                   asst("done", 5)])
        st = S.build(T.parse(p))

        ctx = EC.build(p, st, "session", question="keeper yield 是多少",
                       project_context=True)

        self.assertIn("## Git summary", ctx.text)
        self.assertIn("## Project file excerpts", ctx.text)
        self.assertIn("[src/metrics.txt:L2] keeper yield: 73.2%", ctx.text)
        self.assertIn("`src/metrics.txt`  [tree]", ctx.text)
        self.assertNotIn("unrelated implementation detail", ctx.text)


if __name__ == "__main__":
    unittest.main()
