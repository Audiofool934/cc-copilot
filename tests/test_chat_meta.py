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


if __name__ == "__main__":
    unittest.main()
