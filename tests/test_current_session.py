"""Observing the human's own current (live) session — detection + surfacing."""

import os
import tempfile
import types
import unittest

from cccopilot import cli, locate as LOC, scope as SC


class _SessionEnv(unittest.TestCase):
    KEYS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_CONFIG_DIR")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _home_with_session(self, sid, cwd="/proj/here"):
        home = tempfile.mkdtemp(prefix="cccur-home-")
        d = os.path.join(home, "projects", LOC.encode_cwd(cwd))
        os.makedirs(d)
        p = os.path.join(d, sid + ".jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{}\n")
        os.environ["CLAUDE_CONFIG_DIR"] = home
        return p


class TestDetection(_SessionEnv):
    def test_current_session_id_precedence(self):
        os.environ["CLAUDE_SESSION_ID"] = "legacy"
        self.assertEqual(LOC.current_session_id(), "legacy")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "newvar"
        self.assertEqual(LOC.current_session_id(), "newvar")   # new name wins

    def test_current_session_id_empty(self):
        self.assertEqual(LOC.current_session_id(), "")

    def test_current_session_path_found_by_id(self):
        sid = "11111111-2222-3333-4444-555555555555"
        p = self._home_with_session(sid)
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        self.assertEqual(LOC.current_session_path(), p)

    def test_current_session_path_none_when_absent(self):
        os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
        os.environ["CLAUDE_CODE_SESSION_ID"] = "nope"
        self.assertIsNone(LOC.current_session_path())


class TestSurfacing(_SessionEnv):
    def test_mark_existing_ref_live(self):
        refs = [LOC.SessionRef("/p/a.jsonl", "aaa", 2, 1, agent="claude"),
                LOC.SessionRef("/p/b.jsonl", "bbb", 1, 1, agent="claude")]
        os.environ["CLAUDE_CODE_SESSION_ID"] = "bbb"
        SC._mark_current_session(refs, here="/p/a.jsonl")
        self.assertTrue([r for r in refs if r.session_id == "bbb"][0].live)
        self.assertFalse([r for r in refs if r.session_id == "aaa"][0].live)

    def test_inject_cross_project_live_session(self):
        sid = "99999999-0000-0000-0000-000000000000"
        self._home_with_session(sid, cwd="/proj/elsewhere")
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        refs = [LOC.SessionRef("/other/x.jsonl", "xxx", 1, 1, agent="claude")]
        SC._mark_current_session(refs, here="/other/x.jsonl")
        live = [r for r in refs if r.live]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0].session_id, sid)   # the cross-project current session

    def test_no_injection_without_current(self):
        refs = [LOC.SessionRef("/p/a.jsonl", "aaa", 1, 1)]
        SC._mark_current_session(refs, here="/p/a.jsonl")   # no env set
        self.assertFalse(any(r.live for r in refs))

    def test_picker_label_marks_live(self):
        from cccopilot import tui
        ref = types.SimpleNamespace(title="my work", session_id="abc12345",
                                    size=1024, path="/p/x.jsonl", agent="claude", live=True)
        self.assertIn("live session", tui._session_picker_label(ref))


class TestHereFlag(_SessionEnv):
    def test_here_resolves_to_current_session(self):
        sid = "abababab-cdcd-efef-0101-202020202020"
        p = self._home_with_session(sid)
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        args = types.SimpleNamespace(cwd=None, here=True)
        self.assertEqual(cli._resolve_or_die(args), p)

    def test_here_flag_parses(self):
        a = cli.build_parser().parse_args(["cockpit", "--here"])
        self.assertTrue(a.here)


if __name__ == "__main__":
    unittest.main()
