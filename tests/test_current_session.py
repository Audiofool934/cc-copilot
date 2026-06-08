"""Observing the human's own current (live) session — detection + surfacing."""

import os
import tempfile
import types
import unittest

from cccopilot import cli, locate as LOC, scope as SC

try:
    import textual  # noqa: F401  — probe first; cccopilot.tui sys.exits without it
    from cccopilot import tui
    HAVE_TEXTUAL = True
except Exception:                                   # pragma: no cover
    HAVE_TEXTUAL = False


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

    def test_inject_cross_project_live_session_for_picker(self):
        sid = "99999999-0000-0000-0000-000000000000"
        self._home_with_session(sid, cwd="/proj/elsewhere")
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        refs = [LOC.SessionRef("/other/x.jsonl", "xxx", 1, 1, agent="claude")]
        SC._mark_current_session(refs, here="/other/x.jsonl", inject=True)
        live = [r for r in refs if r.live]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0].session_id, sid)   # the cross-project current session

    def test_evidence_does_not_inject_cross_project_live(self):
        # without inject (evidence path), a foreign-project session never leaks in
        sid = "88888888-0000-0000-0000-000000000000"
        self._home_with_session(sid, cwd="/proj/elsewhere")
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        refs = [LOC.SessionRef("/other/x.jsonl", "xxx", 1, 1, agent="claude")]
        SC._mark_current_session(refs, here="/other/x.jsonl", inject=False)
        self.assertEqual(len(refs), 1)
        self.assertFalse(any(r.live for r in refs))

    def test_no_injection_without_current(self):
        refs = [LOC.SessionRef("/p/a.jsonl", "aaa", 1, 1)]
        SC._mark_current_session(refs, here="/p/a.jsonl", inject=True)   # no env set
        self.assertFalse(any(r.live for r in refs))

    def test_picker_puts_live_session_first(self):
        import json
        home = tempfile.mkdtemp(prefix="cclive-")
        cwd = "/proj/work"
        d = os.path.join(home, "projects", LOC.encode_cwd(cwd))
        os.makedirs(d)
        anchor = os.path.join(d, "anchor.jsonl")
        live = os.path.join(d, "livesess.jsonl")
        for p, sid in [(anchor, "anchor"), (live, "livesess")]:
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "user", "sessionId": sid, "cwd": cwd,
                                    "message": {"role": "user", "content": "hi"}}) + "\n")
        os.utime(anchor, (3000, 3000))   # anchor is NEWER by mtime…
        os.utime(live, (1000, 1000))     # …yet the live session must still sort first
        os.environ["CLAUDE_CONFIG_DIR"] = home
        os.environ["CLAUDE_CODE_SESSION_ID"] = "livesess"
        refs = SC._candidate_refs(anchor, inject_current=True)
        self.assertTrue(refs[0].live)
        self.assertEqual(refs[0].session_id, "livesess")
        # evidence path is NOT reordered (only the picker pins live to the top)
        ev = SC._candidate_refs(anchor, inject_current=False)
        self.assertFalse(getattr(ev[0], "live", False))

    def test_current_session_path_prefers_newest_duplicate(self):
        import os.path as _p
        sid = "77777777-0000-0000-0000-000000000000"
        home = tempfile.mkdtemp(prefix="cccur-dup-")
        older = os.path.join(home, "projects", LOC.encode_cwd("/proj/old"))
        newer = os.path.join(home, "projects", LOC.encode_cwd("/proj/new"))
        os.makedirs(older); os.makedirs(newer)
        po = os.path.join(older, sid + ".jsonl"); pn = os.path.join(newer, sid + ".jsonl")
        for p in (po, pn):
            open(p, "w").write("{}\n")
        os.utime(po, (1000, 1000)); os.utime(pn, (2000, 2000))
        os.environ["CLAUDE_CONFIG_DIR"] = home
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        self.assertEqual(LOC.current_session_path(), pn)   # most recently written

    @unittest.skipUnless(HAVE_TEXTUAL, "textual extra not installed")
    def test_picker_label_marks_live(self):
        ref = types.SimpleNamespace(title="my work", session_id="abc12345",
                                    size=1024, path="/p/x.jsonl", agent="claude", live=True)
        self.assertIn("live session", tui._session_picker_label(ref))


class TestSwitchHereScope(_SessionEnv):
    def test_switch_to_here_resets_scope(self):
        import json
        from cccopilot import chat as C
        from tests.util import user, asst, write
        sid = "33333333-0000-0000-0000-000000000000"
        home = tempfile.mkdtemp(prefix="cchere-")
        d = os.path.join(home, "projects", LOC.encode_cwd("/proj/live"))
        os.makedirs(d)
        live = os.path.join(d, sid + ".jsonl")
        with open(live, "w", encoding="utf-8") as f:
            for e in [user("live work", 30, sessionId=sid, cwd="/proj/live"),
                      asst("ok", 5)]:
                f.write(json.dumps(e) + "\n")
        os.environ["CLAUDE_CONFIG_DIR"] = home
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid

        other = write([user("other", 60), asst("done", 5)])
        s = C.ChatSession(other, alerts=False, persist=False)
        s.scope = SC.MULTI                 # a wider scope with stale, foreign selectors
        s.scope_sessions = ["stale-from-old-project"]

        p = s.switch_to_here()
        self.assertEqual(os.path.abspath(p), os.path.abspath(live))
        self.assertEqual(s.scope, SC.SESSION)      # reset to single session
        self.assertEqual(s.scope_sessions, [])     # stale selectors cleared


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
