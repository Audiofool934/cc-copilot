"""Integration tests for persistent copilot history across ChatSession, config,
and the CLI — the user-visible behaviour: switching sessions (and relaunching)
restores prior dialogue instead of losing it. No real LLM is called.
"""

import io
import json
import os
import tempfile
import types
import unittest
from contextlib import redirect_stdout

from cccopilot import chat as C, narrate as N, config as CFG, store as ST
from tests.util import write, user, asst


def _tx(sid, cwd="/test/proj"):
    """A throwaway transcript with an explicit session id (=> conv_id)."""
    return write([user("task", 100, sessionId=sid, cwd=cwd),
                  asst("ok", 50), asst("done", 5)])


class _StateHome(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cchist-")
        self._env = {k: os.environ.get(k) for k in
                     ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY", "CC_COPILOT_CONFIG")}
        os.environ["CC_COPILOT_STATE_DIR"] = self.home
        os.environ["CC_COPILOT_HISTORY"] = "1"          # force persistence on
        os.environ["CC_COPILOT_CONFIG"] = os.path.join(self.home, "none.toml")
        self._realchat = N.chat
        N.chat = lambda st, history, q, model=None, backend=None: f"A:{q}"

    def tearDown(self):
        N.chat = self._realchat
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestRestore(_StateHome):
    def test_switch_restores_prior_dialogue(self):
        a, b = _tx("sess-A"), _tx("sess-B")
        s = C.ChatSession(a, backend="codex", alerts=False)
        s.answer("q1 on A")
        s.answer("q2 on A")
        s.switch_path(b)
        self.assertEqual(s.history, [])                 # B is fresh
        s.answer("q1 on B")
        s.switch_path(a)                                # ← was the data-loss point
        self.assertEqual([t for r, t in s.history if r == "user"],
                         ["q1 on A", "q2 on A"])
        s.switch_path(b)
        self.assertEqual([t for r, t in s.history if r == "user"], ["q1 on B"])

    def test_relaunch_restores(self):
        a = _tx("sess-A")
        C.ChatSession(a, alerts=False).answer("hello there")
        fresh = C.ChatSession(a, alerts=False)          # new "process"
        self.assertEqual(fresh.history[:2],
                         [("user", "hello there"), ("assistant", "A:hello there")])

    def test_switch_message_reports_restore(self):
        a, b = _tx("sess-A"), _tx("sess-B")
        s = C.ChatSession(a, alerts=False)
        s.answer("hi")
        s.answer("again")
        s.switch_path(b)                                # now on B (no prior chat)
        s._listing = [a, b]                             # control /use's session list
        msg = s._switch(os.path.basename(a)[:-6])       # switch back to A by id
        self.assertIn("restored 2 prior turns", msg)

    def test_single_append_site(self):
        a = _tx("sess-A")
        s = C.ChatSession(a, alerts=False)
        s.answer("one")
        s.answer("two")
        with open(s.store.turns_path, encoding="utf-8") as fh:
            turns = [l for l in fh if json.loads(l).get("kind") == "turn"]
        self.assertEqual(len(turns), 2)                 # no double-logging

    def test_transcript_gone_is_history_only(self):
        a = _tx("sess-A")
        s = C.ChatSession(a, alerts=False)
        s.answer("remember this")
        os.remove(a)
        header = [h for h in ST.list_conversations(None) if h.conv_id == "sess-A"][0]
        self.assertFalse(header.transcript_present)
        live = C.ChatSession(_tx("sess-B"), alerts=False)
        is_live = live.attach_conv(header)
        self.assertFalse(is_live)
        self.assertIsNone(live.st)
        self.assertEqual(len(live.history), 2)          # still viewable

    def test_rewind_truncates_and_persists(self):
        a = _tx("sess-A")
        s = C.ChatSession(a, alerts=False)
        s.answer("q0"); s.answer("q1"); s.answer("q2")
        out = s.meta("/rewind 2")                        # re-ask message #2 (keep turn 0)
        self.assertIn("rewound", out)
        self.assertIn("q1", out)
        self.assertEqual([t for r, t in s.history if r == "user"], ["q0"])
        # persisted across a fresh session
        fresh = C.ChatSession(a, alerts=False)
        self.assertEqual([t for r, t in fresh.history if r == "user"], ["q0"])

    def test_rewind_lists_without_arg(self):
        a = _tx("sess-A")
        s = C.ChatSession(a, alerts=False)
        s.answer("first question"); s.answer("second question")
        out = s.meta("/rewind")
        self.assertIn("1. first question", out)
        self.assertIn("2. second question", out)

    def test_history_only_refresh_does_not_raise(self):
        a = _tx("sess-A")
        s = C.ChatSession(a, alerts=False)
        s.answer("q")
        os.remove(a)
        header = [h for h in ST.list_conversations(None) if h.conv_id == "sess-A"][0]
        s.attach_conv(header)                           # history-only (st is None)
        self.assertFalse(s.refresh())                   # tolerates the gone file
        self.assertIsNone(s.st)
        # the LLM-free commands degrade gracefully instead of crashing
        self.assertIn("history-only", s.meta("/brief"))
        self.assertIn("history-only", s.meta("/diff"))
        self.assertIn("transcript gone", s.banner())

    def test_opt_out_persists_nothing(self):
        a = _tx("sess-A")
        s = C.ChatSession(a, alerts=False, persist=False)
        s.answer("secret")
        self.assertFalse(os.path.exists(s.store.turns_path))
        self.assertEqual(C.ChatSession(a, alerts=False, persist=False).history, [])

    def test_opt_out_does_not_restore_prior_history(self):
        # prior session saved history; a later --no-persist session must NOT
        # read it back (Codex P1: privacy/in-memory-only must be honored)
        a = _tx("sess-A")
        C.ChatSession(a, alerts=False).answer("prior secret")   # persisted
        s = C.ChatSession(a, alerts=False, persist=False)
        self.assertEqual(s.history, [])
        self.assertIn("history is off", s.meta("/history all"))


class TestConfigToggle(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("CC_COPILOT_HISTORY", "CC_COPILOT_CONFIG")}
        self.cfg = tempfile.NamedTemporaryFile(suffix=".toml", delete=False, mode="w")

    def tearDown(self):
        os.unlink(self.cfg.name)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_env_overrides_file(self):
        self.cfg.write("[history]\nenabled = true\n"); self.cfg.close()
        os.environ["CC_COPILOT_CONFIG"] = self.cfg.name
        os.environ["CC_COPILOT_HISTORY"] = "0"
        self.assertFalse(CFG.history_enabled())
        os.environ["CC_COPILOT_HISTORY"] = "yes"
        self.assertTrue(CFG.history_enabled())

    def test_file_disables(self):
        self.cfg.write("[history]\nenabled = false\n"); self.cfg.close()
        os.environ["CC_COPILOT_CONFIG"] = self.cfg.name
        os.environ.pop("CC_COPILOT_HISTORY", None)
        self.assertFalse(CFG.history_enabled())

    def test_default_on(self):
        self.cfg.write("backend = \"codex\"\n"); self.cfg.close()
        os.environ["CC_COPILOT_CONFIG"] = self.cfg.name
        os.environ.pop("CC_COPILOT_HISTORY", None)
        self.assertTrue(CFG.history_enabled())

    def test_fallback_parser_nests_sections(self):
        # the py<3.11 path (no tomllib) must nest [history] like tomllib does
        self.cfg.write("backend = \"codex\"\n[history]\nenabled = false\n"
                       "[env]\nK = \"v\"\n"); self.cfg.close()
        d = CFG._load_simple(self.cfg.name)
        self.assertEqual(d["backend"], "codex")
        self.assertEqual(d["history"], {"enabled": False})
        self.assertEqual(d["env"], {"K": "v"})


class TestCliHistory(_StateHome):
    def test_history_command_lists(self):
        C.ChatSession(_tx("sess-A", cwd="/proj/alpha"), alerts=False).answer("研究一下解析器")
        args = types.SimpleNamespace(all=True, cwd=None)
        from cccopilot import cli
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_history(args)
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("sess-A"[:8], out)
        self.assertIn("研究一下解析器", out)

    def test_history_empty(self):
        args = types.SimpleNamespace(all=False, cwd="/nonexistent/proj")
        from cccopilot import cli
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_history(args)
        self.assertEqual(rc, 1)
        self.assertIn("no saved copilot conversations", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
