import os
import tempfile
import unittest

from cccopilot import context as EC, state as S, transcript as T
from tests.util import asst, result, tool, user, write


class TestEvidenceContext(unittest.TestCase):
    def test_default_context_budget_is_256k_and_env_can_override(self):
        p = write([user("task", 60), asst("done", 5)])
        st = S.build(T.parse(p))
        old = os.environ.get("CC_COPILOT_CONTEXT_TOKENS")
        try:
            os.environ.pop("CC_COPILOT_CONTEXT_TOKENS", None)
            self.assertEqual(EC.build(p, st, "session").stats.budget_tokens, 256000)

            os.environ["CC_COPILOT_CONTEXT_TOKENS"] = "128000"
            self.assertEqual(EC.build(p, st, "session").stats.budget_tokens, 128000)
        finally:
            if old is None:
                os.environ.pop("CC_COPILOT_CONTEXT_TOKENS", None)
            else:
                os.environ["CC_COPILOT_CONTEXT_TOKENS"] = old

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

    def test_raw_evidence_leads_the_pack(self):
        # Position-aware packing: raw cited records sit at the HEAD of the pack
        # (out of the lost-in-the-middle zone) and before lower-priority status
        # facts, so they're never truncated for them under budget pressure.
        p = write([
            user("run tests", 60),
            tool("Bash", {"command": "pytest"}, "t1", 30),
            result("t1", "ok", ago=20),
            asst("done", 5),
        ])
        st = S.build(T.parse(p))
        ctx = EC.build(p, st, "session", question="what happened?")
        self.assertTrue(ctx.text.lstrip().startswith("## Primary Raw Transcript Evidence"))
        raw_i = ctx.text.index("## Primary Raw Transcript Evidence")
        target_i = ctx.text.index("## Target Context")
        status_i = ctx.text.index("## Current Status Facts")
        self.assertLess(raw_i, target_i)
        self.assertLess(target_i, status_i)
        self.assertLess(raw_i, status_i)

    def test_target_context_names_supervisor_boundary_and_target(self):
        p = write([user("task", 60), asst("done", 5)])
        st = S.build(T.parse(p))

        ctx = EC.build(p, st, "session", question="what happened?",
                       project_context=False)

        self.assertIn("## Target Context", ctx.text)
        self.assertIn("copilot role: read-only supervisor; not the target agent", ctx.text)
        self.assertIn("target scope: `session`", ctx.text)
        self.assertIn("no hidden agent context, no tool access, no prompt injection", ctx.text)
        self.assertIn("project context: not included", ctx.text)
        self.assertIn("target session: `claude` session `testsess`", ctx.text)

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

    def test_recent_cockpit_question_guides_followup_raw_retrieval(self):
        p = write([
            user("start analysis", 120),
            asst("buried topic: zebra-stripe allocator returned score 17", 115),
            *[asst(f"unrelated filler {i}", 110 - i) for i in range(18)],
        ])
        st = S.build(T.parse(p))
        history = [
            ("user", "what did the agent say about the zebra-stripe allocator?"),
            ("assistant", "It mentioned score 17."),
        ]

        ctx = EC.build(p, st, "session", question="what about that?",
                       history=history, project_context=False)

        self.assertIn("zebra-stripe allocator returned score 17", ctx.text)
        self.assertIn("keyword match", ctx.text)

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

    def test_recent_cockpit_question_guides_followup_project_excerpt(self):
        cwd = tempfile.mkdtemp(prefix="ccctx-project-followup-")
        os.makedirs(os.path.join(cwd, "src"))
        with open(os.path.join(cwd, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Demo Project\n")
        with open(os.path.join(cwd, "src", "onboard.txt"), "w", encoding="utf-8") as f:
            f.write("ollama cloud onboarding uses hosted model credentials\n")
        p = write([user("inspect project", 60, sessionId="testsess", cwd=cwd),
                   asst("done", 5)])
        st = S.build(T.parse(p))
        history = [
            ("user", "look at ollama cloud onboarding support"),
            ("assistant", "The relevant area is the onboarding model path."),
        ]

        ctx = EC.build(p, st, "session", question="那这个怎么处理?",
                       history=history, project_context=True)

        self.assertIn("[src/onboard.txt:L1] ollama cloud onboarding", ctx.text)


    def test_assistant_path_hints_do_not_guide_followup_project_excerpt(self):
        cwd = tempfile.mkdtemp(prefix="ccctx-project-assistant-path-")
        os.makedirs(os.path.join(cwd, "config"))
        with open(os.path.join(cwd, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Demo Project\n")
        with open(os.path.join(cwd, "config", "local_settings.py"), "w", encoding="utf-8") as f:
            f.write('INTERNAL_SUPPORT_PIN = "blue-otter-4931"\n')
        p = write([user("inspect project", 60, sessionId="testsess", cwd=cwd),
                   asst("done", 5)])
        st = S.build(T.parse(p))
        history = [
            ("user", "what next?"),
            ("assistant", "Continue with config/local_settings.py."),
        ]

        ctx = EC.build(p, st, "session", question="continue",
                       history=history, project_context=True)

        self.assertNotIn("### `config/local_settings.py`", ctx.text)
        self.assertNotIn("blue-otter-4931", ctx.text)

    def test_project_context_excludes_common_secret_files(self):
        cwd = tempfile.mkdtemp(prefix="ccctx-project-secrets-")
        with open(os.path.join(cwd, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Public Notes\n")
        secret_files = {
            ".npmrc": "//registry.npmjs.org/:_authToken=npm_secret_token\n",
            ".pypirc": "password = pypi_secret_token\n",
            ".netrc": "machine example.com password netrc_secret_token\n",
            "service_account.json": '{"private_key": "service_secret_token"}\n',
            "prod.token": "prod_secret_token\n",
        }
        for rel, text in secret_files.items():
            with open(os.path.join(cwd, rel), "w", encoding="utf-8") as f:
                f.write(text)
        p = write([user("inspect project", 60, sessionId="testsess", cwd=cwd),
                   asst("done", 5)])
        st = S.build(T.parse(p))

        ctx = EC.build(p, st, "session", question="summarize project",
                       project_context=True)

        self.assertIn("Public Notes", ctx.text)
        for rel, text in secret_files.items():
            self.assertNotIn(rel, ctx.text)
            self.assertNotIn(text.strip(), ctx.text)


class TestContextHistoryRobustness(unittest.TestCase):
    def test_none_history_text_does_not_crash(self):
        # a null "q"/"a" in cockpit history used to TypeError the citation join.
        p = write([user("inspect", 60, sessionId="s", cwd="/test/proj"),
                   asst("done", 5)])
        st = S.build(T.parse(p))
        history = [("user", "earlier ask"), ("assistant", None), ("user", "")]
        ctx = EC.build(p, st, "session", question="what now?", history=history,
                       project_context=False)
        self.assertTrue(ctx.text)


if __name__ == "__main__":
    unittest.main()
