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


def _tx_in_dir(dir_, sid, title="", cwd="/test/proj"):
    p = os.path.join(dir_, sid + ".jsonl")
    events = [user("task", 100, sessionId=sid, cwd=cwd),
              asst("ok", 50), asst("done", 5)]
    if title:
        events.append({"type": "custom-title", "customTitle": title})
    with open(p, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return p


class _StateHome(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cchist-")
        self._env = {k: os.environ.get(k) for k in
                     ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY", "CC_COPILOT_CONFIG")}
        os.environ["CC_COPILOT_STATE_DIR"] = self.home
        os.environ["CC_COPILOT_HISTORY"] = "1"          # force persistence on
        os.environ["CC_COPILOT_CONFIG"] = os.path.join(self.home, "none.toml")
        self._realchat = N.chat
        self._realchat_brief = N.chat_brief
        N.chat = lambda st, history, q, model=None, backend=None: f"A:{q}"
        N.chat_brief = lambda brief, history, q, model=None, backend=None: f"A:{q}"

    def tearDown(self):
        N.chat = self._realchat
        N.chat_brief = self._realchat_brief
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestRestore(_StateHome):
    def test_switch_keeps_current_cockpit_dialogue(self):
        a, b = _tx("sess-A"), _tx("sess-B")
        s = C.ChatSession(a, backend="codex", alerts=False)
        s.answer("q1 on A")
        s.answer("q2 on A")
        s.switch_path(b)
        self.assertEqual([t for r, t in s.history if r == "user"],
                         ["q1 on A", "q2 on A"])
        s.answer("q1 after switching evidence")
        s.switch_path(a)                                # ← was the data-loss point
        self.assertEqual([t for r, t in s.history if r == "user"],
                         ["q1 on A", "q2 on A", "q1 after switching evidence"])
        s.switch_path(b)
        self.assertEqual([t for r, t in s.history if r == "user"],
                         ["q1 on A", "q2 on A", "q1 after switching evidence"])

    def test_relaunch_restores(self):
        a = _tx("sess-A")
        C.ChatSession(a, alerts=False).answer("hello there")
        fresh = C.ChatSession(a, alerts=False)          # new "process"
        self.assertEqual(fresh.history[:2],
                         [("user", "hello there"), ("assistant", "A:hello there")])

    def test_switch_message_reports_evidence_change(self):
        a, b = _tx("sess-A"), _tx("sess-B")
        s = C.ChatSession(a, alerts=False)
        s.answer("hi")
        s.answer("again")
        s._listing = [a, b]                             # control /use's session list
        msg = s._switch(os.path.basename(b)[:-6])
        self.assertIn("cockpit chat kept", msg)
        self.assertEqual([t for r, t in s.history if r == "user"], ["hi", "again"])

    def test_resume_restores_scope_and_dialogue(self):
        a = _tx("sess-A")
        s = C.ChatSession(a, alerts=False)
        sid = os.path.basename(a)[:-6]
        s.meta(f"/scope multi {sid}")
        s.answer("remember scoped cockpit")
        header = [h for h in ST.list_conversations(None) if h.conv_id == s.store.conv_id][0]

        fresh = C.ChatSession(_tx("sess-B"), alerts=False)
        live = fresh.attach_conv(header)

        self.assertTrue(live)
        self.assertEqual(fresh.scope, "multi-session")
        self.assertEqual(fresh.scope_sessions, [sid])
        self.assertEqual([t for r, t in fresh.history if r == "user"],
                         ["remember scoped cockpit"])

    def test_session_list_shows_renamed_titles(self):
        d = tempfile.mkdtemp(prefix="ccsess-")
        a = _tx_in_dir(d, "sess-A", title="test-session-A")
        _tx_in_dir(d, "sess-B", title="test-session-B")
        s = C.ChatSession(a, alerts=False)
        out = s.meta("/sessions")
        self.assertIn("test-session-A", out)
        self.assertIn("test-session-B", out)

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

    def test_rewind_undo_restores_truncated_history(self):
        a = _tx("sess-A")
        s = C.ChatSession(a, alerts=False)
        s.answer("q0"); s.answer("q1"); s.answer("q2")
        s.meta("/rewind 2")

        out = s.meta("/rewind undo")

        self.assertIn("restored", out)
        self.assertEqual([t for r, t in s.history if r == "user"],
                         ["q0", "q1", "q2"])
        fresh = C.ChatSession(a, alerts=False)
        self.assertEqual([t for r, t in fresh.history if r == "user"],
                         ["q0", "q1", "q2"])

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

    def test_answer_records_context_usage_estimates(self):
        a = _tx("sess-A")
        s = C.ChatSession(a, alerts=False, persist=False)

        ans = s.answer("what happened?")

        self.assertEqual(ans, "A:what happened?")
        self.assertIsNotNone(s.last_context_stats)
        self.assertGreater(s.last_context_stats.estimated_tokens, 0)
        self.assertGreater(s.last_context_stats.raw_tokens, 0)
        self.assertGreater(s.last_output_tokens, 0)

    def test_large_history_is_compacted_into_prompt_memory(self):
        seen = []
        N.chat_brief = lambda brief, history, q, model=None, backend=None: \
            seen.append(brief) or "answer [sess-A:L2]"
        a = _tx("sess-A")
        s = C.ChatSession(a, alerts=False)
        long = "x" * 5000
        for i in range(9):
            s.answer(f"should keep project read-only decision {i}? {long}")

        s.answer("what did we decide?")

        self.assertIn("## Durable Cockpit Memory", seen[-1])
        self.assertIn("project read-only", seen[-1])
        self.assertTrue(os.path.exists(s.store.memory_path))
        self.assertEqual(len(s.store.load_history()), 20)


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
        self.assertIn("resumable cockpit sessions", out)

    def test_history_empty(self):
        args = types.SimpleNamespace(all=False, cwd="/nonexistent/proj")
        from cccopilot import cli
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_history(args)
        self.assertEqual(rc, 1)
        self.assertIn("no resumable cockpit sessions", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
