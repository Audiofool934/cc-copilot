"""REPL meta-command surface (chat.py): the /target rename, /status fleet board,
and that the one-keystroke-collision /session spelling is gone."""

import os
import tempfile
import unittest

from cccopilot import chat as C
from tests.util import asst, user, write


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
