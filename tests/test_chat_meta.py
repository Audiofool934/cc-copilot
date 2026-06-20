"""REPL meta-command surface (chat.py): the /target rename, /status fleet board,
and that the one-keystroke-collision /session spelling is gone."""

import os
import tempfile
import unittest

from cccopilot import chat as C
from tests.util import asst, tool, user, write


class TestSinceRecapLeadsWithPendingAsk(unittest.TestCase):
    def test_compose_hoists_unanswered_ask_above_the_recap(self):
        from cccopilot import since as SI, state as S, transcript as T
        p = write([user("first", 300), asst("ok", 250),
                   user("add the export feature", 20)])
        st = S.build(T.parse(p))
        view = SI.build(st.tr, st, since_line=2, label="last look")
        self.assertTrue(view.pending_ask)                  # cue captured on the view
        composed = C.ChatSession._compose_since("THE-LLM-RECAP", view)
        self.assertLess(composed.index("still unanswered"),
                        composed.index("THE-LLM-RECAP"))    # cue leads the narration


class TestSubagentRollup(unittest.TestCase):
    def test_rollup_summarizes_children_by_status_and_flags_needy(self):
        idle_child = write([user("sub task", 200), asst("subagent done", 60)])
        running_child = write([user("sub task", 100),
                               tool("Bash", {"command": "sleep"}, "t1", 1)])
        line = C._subagent_rollup([running_child, idle_child])
        self.assertIn("subagents:", line)
        self.assertIn("1 running", line)
        self.assertIn("1 idle", line)

    def test_rollup_caps_and_notes_overflow(self):
        kids = [write([user("k", 50), asst("ok", 5)]) for _ in range(C._SUB_CAP + 3)]
        line = C._subagent_rollup(kids)
        self.assertIn(f"(+{3} more)", line)

    def test_rollup_flags_review_worthy_idle_child(self):
        # idle child that had a failed test → verdict review → must be flagged,
        # not rendered as a plain "idle".
        from tests.util import result
        needy = write([
            user("run tests", 120),
            tool("Bash", {"command": "pytest"}, "t1", 60),
            result("t1", "1 failed", is_error=True, ago=55),
            asst("a test is failing", 5),
        ])
        line = C._subagent_rollup([needy])
        self.assertIn("need a look", line)


class TestReplMetaCommands(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k)
                     for k in ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY")}
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="ccrepl-")
        os.environ.pop("CC_COPILOT_HISTORY", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _sess(self):
        p = write([user("go", 120), asst("done", 5)])
        s = C.ChatSession(p, backend="codex", alerts=False, persist=False)
        s.refresh()
        return s

    def test_target_shows_the_current_cockpit_readout(self):
        s = self._sess()
        out = s.meta("/target")
        self.assertIn("cockpit:", out)
        self.assertIn("target:", out)
        self.assertIn("evidence:", out)

    def test_singular_session_spelling_is_gone(self):
        # /session collided one keystroke from /sessions and meant something
        # different per surface; it was renamed to /target and removed.
        s = self._sess()
        self.assertIn("unknown command", s.meta("/session"))

    def test_status_renders_a_fleet_board_string(self):
        s = self._sess()
        out = s.meta("/status")
        self.assertIsInstance(out, str)
        # the fixture cwd has no project sessions on disk → graceful empty board,
        # not a crash; either way it is the fleet surface, not an error.
        self.assertTrue("status" in out.lower() or "no work sessions" in out)

    def test_sessions_plural_still_lists_evidence(self):
        s = self._sess()
        self.assertIn("agent sessions", s.meta("/sessions"))

    def test_scope_groups_save_load_list_delete(self):
        s = self._sess()
        self.assertIn("scope → project", s.meta("/scope project"))
        self.assertIn("saved scope group release → project",
                      s.meta("/scope save release"))
        self.assertIn("release → project", s.meta("/scope groups"))

        self.assertIn("scope → session", s.meta("/scope session"))
        self.assertEqual(s.scope, "session")

        out = s.meta("/scope load release")
        self.assertIn("scope group release → project", out)
        self.assertEqual(s.scope, "project")

        self.assertIn("deleted scope group release", s.meta("/scope delete release"))
        self.assertIn("no saved scope group", s.meta("/scope load release"))

    def test_goal_without_backend_returns_paste_ready_deterministic_goal(self):
        from cccopilot import narrate as N
        real_avail = N.available
        N.available = lambda be=None: False
        try:
            s = self._sess()
            out = s.meta("/goal focus verification")
        finally:
            N.available = real_avail
        self.assertIn("/goal ", out)
        self.assertIn("focus verification", out)
        self.assertIn("cc-copilot does not inject", out)
        self.assertIn(("user", "/goal focus verification"), s.history)
        self.assertTrue(any(role == "assistant" and "focus verification" in text
                            for role, text in s.history))

    def test_goal_with_backend_uses_goal_narration_and_fallback_anchor(self):
        from cccopilot import narrate as N
        real_avail, real_goal = N.available, N.goal_brief
        N.available = lambda be=None: True
        N.goal_brief = lambda text, model=None, backend=None, instruction="": (
            "```text\n/goal MODEL_GOAL\n```\n\nWhy this goal\n- cited [L1]"
        )
        try:
            s = self._sess()
            out = s.meta("/goal tests first")
        finally:
            N.available, N.goal_brief = real_avail, real_goal
        self.assertIn("MODEL_GOAL", out)
        self.assertIn("deterministic fallback", out)
        self.assertIn("/goal ", out)

    def test_loop_output_is_available_to_the_next_question_context(self):
        from cccopilot import narrate as N
        real_avail, real_chat = N.available, N.chat_brief
        captured = {}

        def fake_chat(text, snippets, q, model=None, backend=None):
            captured["text"] = text
            captured["q"] = q
            return "updated loop prompt"

        N.available = lambda be=None: False
        N.chat_brief = fake_chat
        try:
            s = self._sess()
            out = s.meta("/loop add checkpoints")
            ans = s.answer("修改一下，加一点重试")
        finally:
            N.available, N.chat_brief = real_avail, real_chat
        self.assertIn("/loop ", out)
        self.assertEqual(ans, "updated loop prompt")
        self.assertIn(("user", "/loop add checkpoints"), s.history)
        self.assertIn("/loop add checkpoints", captured["text"])
        self.assertIn("add checkpoints", captured["text"])
        self.assertIn("/loop ", captured["text"])

    def test_failed_model_error_is_not_treated_as_assistant_context(self):
        s = self._sess()
        s.history = [
            ("user", "上一句失败的问题"),
            ("error", "# error: deepseek connection error: [Errno 104] Connection reset by peer"),
        ]

        ctx = s.answer_context("再试一次").text

        self.assertIn("上一句失败的问题", ctx)
        self.assertNotIn("Connection reset by peer", ctx)

    def test_repl_error_turn_is_rewindable_but_not_persisted(self):
        s = self._sess()
        s.record_error_turn("will this retry?", "# error: connection reset")

        out = s.meta("/rewind 1")

        self.assertIn("will this retry?", out)
        self.assertEqual(s.history, [])
        self.assertEqual(s.store._load_turns(), [])


if __name__ == "__main__":
    unittest.main()
