"""The Copilot facade: parity with the CLI presenters and the read-only contract.

The load-bearing property is parity - the facade must produce the same Markdown
the CLI prints, because the GUI is meant to be a third presenter over the same
core, not a fork of it. These tests pin that by calling the exact functions the
CLI calls (``cmd_brief``/``cmd_check``/``cmd_observe``/``cmd_since``) and
asserting the facade matches.

The ``session=<path>`` form exercises ``sources.resolve``'s isfile shortcut, so
the reading-surface tests need no fake agent home. Discovery and last-look tests
set up isolated temp homes so they don't depend on the real ``~/.claude`` /
``~/.codex`` / state dirs on the runner.
"""

import datetime as _dt
import os
import tempfile
import unittest
from unittest import mock

from cccopilot import api as API
from cccopilot import brief as B
from cccopilot import observe as O
from cccopilot import scope as SC
from cccopilot import since as SI
from cccopilot import sources as SRC
from cccopilot import state as S
from cccopilot import transcript as T
from cccopilot.narrate import StreamHandle
from tests.util import asst, result, tool, user, write


# Freeze state.idle_seconds' "now" so it is stable across the two state builds
# the parity tests compare - the facade builds its own State internally, so
# without freezing the clock these tests flake whenever the two builds straddle
# a second boundary. Captured per-test (not a hardcoded date) so it stays just
# after the fixture's last activity and idle stays a small positive value.
def _freeze_now(testcase):
    fixed = _dt.datetime.now(_dt.timezone.utc)
    patcher = mock.patch("cccopilot.state.datetime")
    dt = patcher.start()
    dt.now.return_value = fixed
    testcase.addCleanup(patcher.stop)


# A fixture with real activity: an ask, a command, a file edit, a closing reply.
_FIXTURE = [
    user("add the export feature", 300),
    asst("working on it", 250),
    tool("Bash", {"command": "pytest"}, "t1", 200),
    result("t1", "ok", ago=199),
    tool("Edit", {"file_path": "a.py"}, "t2", 20),
    result("t2", "ok", ago=19),
    asst("done, added export", 5),
]


def _st(path):
    return S.build(T.parse(path))


class TestParity(unittest.TestCase):
    """facade output == the exact function chain the CLI cmd_* use."""

    def setUp(self):
        _freeze_now(self)
        self.path = write(_FIXTURE)
        self.cp = API.Copilot()

    def tearDown(self):
        os.unlink(self.path)

    def test_brief_matches_cli_chain(self):
        st = _st(self.path)
        # cmd_brief computes SC.render_evidence(..., SESSION).text, which for
        # SESSION is B.render(st); the facade must match both.
        self.assertEqual(self.cp.brief(session=self.path), B.render(st))
        self.assertEqual(self.cp.brief(session=self.path),
                         SC.render_evidence(self.path, st, SC.SESSION, sessions="").text)

    def test_check_matches_cli_chain(self):
        st = _st(self.path)
        # cmd_check uses B.render_check(st) for SESSION scope.
        self.assertEqual(self.cp.check(session=self.path), B.render_check(st))

    def test_observe_matches_cli_chain(self):
        st = _st(self.path)
        self.assertEqual(self.cp.observe(session=self.path),
                         O.render(self.path, st, SC.SESSION, sessions=""))

    def test_since_duration_matches_cli_chain(self):
        tr = T.parse(self.path)
        st = S.build(tr)
        self.assertEqual(self.cp.since(session=self.path, when="30m"),
                         SI.build(tr, st, seconds=1800, label="30m").text)

    def test_brief_project_scope_routes_to_render_evidence(self):
        st = _st(self.path)
        # SESSION routes to brief.render; PROJECT must route to render_evidence
        # (the wider-scope path cmd_brief uses for non-SESSION scopes).
        self.assertEqual(self.cp.brief(session=self.path, scope=SC.PROJECT),
                         SC.render_evidence(self.path, st, SC.PROJECT, sessions="").text)
        self.assertEqual(self.cp.brief(session=self.path, scope=SC.SESSION),
                         B.render(st))

    def test_brief_max_files_max_cmds_threaded(self):
        st = _st(self.path)
        # custom caps produce a different (truncated) brief than the default,
        # proving the kwargs reach B.render rather than being dropped.
        default = self.cp.brief(session=self.path)
        capped = self.cp.brief(session=self.path, max_files=0, max_cmds=0)
        self.assertEqual(default, B.render(st, max_files=12, max_cmds=6))
        self.assertEqual(capped, B.render(st, max_files=0, max_cmds=0))


class TestStateAndTranscript(unittest.TestCase):
    def setUp(self):
        _freeze_now(self)
        self.path = write(_FIXTURE)
        self.cp = API.Copilot()

    def tearDown(self):
        os.unlink(self.path)

    def test_transcript_accessor(self):
        tr = self.cp.transcript(self.path)
        self.assertTrue(tr.records)                       # parsed the 7 events
        self.assertEqual(tr.cwd, "/test/proj")            # from the fixture header

    def test_state_accessor(self):
        st = self.cp.state(self.path)
        self.assertEqual(st.tr.cwd, "/test/proj")
        self.assertEqual(st.status, "idle")               # agent gave a closing reply

    def test_state_matches_build_chain(self):
        self.assertEqual(self.cp.state(self.path), S.build(T.parse(self.path)))


class TestCheckVerdict(unittest.TestCase):
    def setUp(self):
        _freeze_now(self)
        self.path = write(_FIXTURE)
        self.cp = API.Copilot()

    def tearDown(self):
        os.unlink(self.path)

    def test_verdict_parity_and_range(self):
        st = _st(self.path)
        v = self.cp.check_verdict(session=self.path)
        self.assertEqual(v, SC.exit_code(self.path, st, SC.SESSION))
        self.assertIn(v, (0, 1, 2))

    def test_clean_fixture_is_clear(self):
        # no friction signals in the fixture -> verdict 0 (clear/idle)
        self.assertEqual(self.cp.check_verdict(session=self.path), 0)


class TestSincePeek(unittest.TestCase):
    """``since(peek=True)`` is read-only; advance is explicit."""

    def setUp(self):
        from cccopilot import lastlook as LL
        self._saved = {k: os.environ.get(k)
                       for k in ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY")}
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="ccapi-ll-")
        os.environ.pop("CC_COPILOT_HISTORY", None)
        self.path = write(_FIXTURE)
        self.cp = API.Copilot()
        self.tr = T.parse(self.path)
        self.key = LL.key_for(getattr(self.tr, "session_id", "") or "", self.path)

    def tearDown(self):
        from cccopilot import lastlook as LL
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # wipe the mark so tests don't bleed into each other
        LL.forget(self.key)
        os.unlink(self.path)

    def test_peek_does_not_create_a_mark(self):
        from cccopilot import lastlook as LL
        before = LL.get(self.key)
        text1 = self.cp.since(session=self.path, when="last-look", peek=True)
        text2 = self.cp.since(session=self.path, when="last-look", peek=True)
        self.assertIsNone(before)
        self.assertIsNone(LL.get(self.key))         # still no mark after two peeks
        self.assertIn("No last-look mark", text1)
        self.assertEqual(text1, text2)               # deterministic

    def test_advance_then_peek_returns_delta(self):
        from cccopilot import lastlook as LL
        self.assertIsNone(LL.get(self.key))
        mark = self.cp.advance_since_mark(session=self.path)
        self.assertIsNotNone(mark)
        self.assertEqual(mark["line"], self.tr.records[-1].line)   # marked at the tail
        # now a peek finds the mark; at the tail there is nothing new
        text = self.cp.since(session=self.path, when="last-look", peek=True)
        self.assertNotIn("No last-look mark", text)

    def test_peek_false_advances_forward_only(self):
        from cccopilot import lastlook as LL
        # establish a mark at the tail, then a non-peek since must not rewind it
        self.cp.advance_since_mark(session=self.path)
        line_before = LL.get(self.key)["line"]
        self.cp.since(session=self.path, when="last-look", peek=False)
        line_after = LL.get(self.key)["line"]
        self.assertGreaterEqual(line_after, line_before)     # never goes backward

    def test_duration_path_is_pure_read(self):
        from cccopilot import lastlook as LL
        before = LL.get(self.key)
        self.cp.since(session=self.path, when="30m")
        self.assertEqual(LL.get(self.key), before)            # duration never touches marks

    def test_bad_when_raises(self):
        with self.assertRaises(ValueError):
            self.cp.since(session=self.path, when="soon")

    def test_diff_returns_structured_delta(self):
        d = self.cp.diff(session=self.path, when="30m")
        self.assertIsInstance(d, dict)
        self.assertGreater(d["new_events"], 0)
        self.assertEqual(d["label"], "30m")
        self.assertTrue(d["new_agent"], "expected the agent's new messages")
        self.assertTrue(d["new_commands"], "expected Bash + Edit commands")
        self.assertTrue(d["new_changed_files"], "expected a.py changed")
        self.assertIsNotNone(d["diff"], "expected a State.Diff transition block")

    def test_diff_no_mark_returns_message(self):
        # last-look with no mark and tracking off -> a status dict, no narration
        import os as _os
        saved = _os.environ.get("CC_COPILOT_HISTORY")
        _os.environ["CC_COPILOT_HISTORY"] = "0"
        try:
            d = self.cp.diff(session=self.path, when="last-look")
            self.assertTrue(d["nothing_new"])
            self.assertIn("tracking is off", d["message"])
        finally:
            if saved is None:
                _os.environ.pop("CC_COPILOT_HISTORY", None)
            else:
                _os.environ["CC_COPILOT_HISTORY"] = saved


class TestResolveAndSessions(unittest.TestCase):
    """Session discovery via a fake Claude home (isolated, no real ~/.claude)."""

    def setUp(self):
        from cccopilot.sources import codex as CX
        self._saved = {k: os.environ.get(k)
                       for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "CC_COPILOT_AGENTS",
                                 "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")}
        self.claude_home = tempfile.mkdtemp(prefix="ccapi-claude-")
        os.makedirs(os.path.join(self.claude_home, "projects"))   # -> ClaudeSource.available()
        self.codex_home = tempfile.mkdtemp(prefix="ccapi-codex-")
        os.makedirs(os.path.join(self.codex_home, "sessions"))    # -> CodexSource.available()
        os.environ["CLAUDE_CONFIG_DIR"] = self.claude_home
        os.environ["CODEX_HOME"] = self.codex_home
        os.environ.pop("CC_COPILOT_AGENTS", None)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        os.environ.pop("CLAUDE_SESSION_ID", None)
        CX._HEAD_CACHE.clear()
        self.cwd = "/tmp/cc-copilot-api-test"
        self.cp = API.Copilot()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(self.claude_home, ignore_errors=True)
        shutil.rmtree(self.codex_home, ignore_errors=True)

    def _write_session(self, sid="api-test-1", content=None):
        from cccopilot import locate as L
        d = os.path.join(self.claude_home, "projects", L.encode_cwd(self.cwd))
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, sid + ".jsonl")   # current_session_path looks up <sid>.jsonl
        with open(p, "w", encoding="utf-8") as f:
            for ev in (content if content is not None else [
                {"type": "user", "cwd": self.cwd, "sessionId": sid,
                 "message": {"role": "user", "content": "go"}},
                {"type": "assistant",
                 "message": {"role": "assistant", "model": "claude",
                             "content": [{"type": "text", "text": "ok"}]}},
            ]):
                f.write(__import__("json").dumps(ev) + "\n")
        os.utime(p, (2000, 2000))
        return p

    def test_sessions_finds_written_session(self):
        p = self._write_session()
        refs = self.cp.sessions(self.cwd)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].path, p)

    def test_resolve_returns_path_for_cwd(self):
        p = self._write_session()
        self.assertEqual(self.cp.resolve(self.cwd), p)

    def test_resolve_unknown_session_returns_none(self):
        self._write_session()
        self.assertIsNone(self.cp.resolve(self.cwd, session="does-not-exist"))

    def test_resolve_missing_cwd_returns_none(self):
        self.assertIsNone(self.cp.resolve("/no/such/cwd/here"))

    def test_session_not_found_raises(self):
        self._write_session()
        with self.assertRaises(API.SessionNotFound):
            self.cp.brief(self.cwd, session="does-not-exist")

    def test_agents_filter_restricts_to_codex_finds_nothing(self):
        self._write_session()                          # a Claude session
        codex_only = API.Copilot(agents=["codex"])
        self.assertEqual(codex_only.sessions(self.cwd), [])   # claude excluded

    def test_agents_filter_claude_finds_it(self):
        p = self._write_session()
        claude_only = API.Copilot(agents=["claude"])
        refs = claude_only.sessions(self.cwd)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].path, p)

    # ---- regression tests for the Codex review findings --------------------

    def _write_codex_session(self, sid="codex-test-1", ago_mtime=3000):
        from tests import util_codex as UX
        sdir = os.path.join(self.codex_home, "sessions", "2026", "06", "07")
        os.makedirs(sdir, exist_ok=True)
        p = UX.write_rollout(
            [UX.session_meta(cwd=self.cwd, sid=sid, big_instructions=False),
             UX.umsg("codex work", 5), UX.amsg("codex done", 1)],
            dir=sdir, name=f"rollout-2026-06-07T10-00-00-{sid}.jsonl")
        os.utime(p, (ago_mtime, ago_mtime))
        return p

    def test_include_current_controls_live_session(self):
        # include_current must control the LIVE session, not helper transcripts.
        p = self._write_session()
        included = self.cp.sessions(self.cwd, include_current=True)
        self.assertEqual(len(included), 1)
        sid = included[0].session_id
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        try:
            self.assertEqual(self.cp.sessions(self.cwd, include_current=False), [])
            self.assertEqual(len(self.cp.sessions(self.cwd, include_current=True)), 1)
        finally:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def test_current_session_path_honors_agents_filter(self):
        p = self._write_session()
        sid = self.cp.sessions(self.cwd, include_current=True)[0].session_id
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        try:
            # a claude-only facade sees the live claude session...
            self.assertEqual(API.Copilot(agents=["claude"]).current_session_path(), p)
            # ...but a codex-only facade does not, even though a session is live
            self.assertIsNone(API.Copilot(agents=["codex"]).current_session_path())
        finally:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def test_wider_scope_agents_filter_excludes_other_agent(self):
        # a Claude and a Codex session for the same project cwd
        from cccopilot import locate as L
        self._write_session(sid="claude-anchor")
        d = os.path.join(self.claude_home, "projects", L.encode_cwd(self.cwd))
        claude_p = os.path.join(d, "claude-anchor.jsonl")
        os.utime(claude_p, (4000, 4000))     # newest -> becomes the anchor
        self._write_codex_session(sid="codex-sibling", ago_mtime=2000)
        # unfiltered multi-session brief spans both agents (2 transcripts)
        both = self.cp.brief(self.cwd, scope=SC.MULTI)
        self.assertIn("2 work-session transcript", both)
        # a claude-only facade must not let the Codex session leak into wider scope
        claude_only = API.Copilot(agents=["claude"])
        only_claude = claude_only.brief(self.cwd, scope=SC.MULTI)
        self.assertIn("1 work-session transcript", only_claude)
        self.assertNotIn("codex-sibling", only_claude)
        self.assertNotIn("codex done", only_claude)


# ---- narration (LLM) surfaces -------------------------------------------

from cccopilot.backends import Backend as _Backend


class _CaptureBackend(_Backend):
    """A minimal in-memory backend for facade narration tests (mirrors the
    test_narrate CaptureBackend). Captures the prompt and returns a fixed
    string; supports both the blocking complete() path and the one-chunk
    streaming fallback (CC_COPILOT_STREAM=0)."""
    name = "capture"

    def __init__(self):
        self.prompts = []

    def available(self):
        return True

    def reason(self):
        return ""

    def complete(self, prompt, model=None, timeout=180):
        self.prompts.append(prompt)
        return "ok"

    def cancel(self):
        pass


class TestNarration(unittest.TestCase):
    def setUp(self):
        _freeze_now(self)
        self.path = write(_FIXTURE)
        self.be = _CaptureBackend()
        self._saved_stream = os.environ.get("CC_COPILOT_STREAM")
        os.environ["CC_COPILOT_STREAM"] = "0"   # one-chunk stream fallback

    def tearDown(self):
        os.environ.pop("CC_COPILOT_STREAM", None)
        if self._saved_stream is not None:
            os.environ["CC_COPILOT_STREAM"] = self._saved_stream
        os.unlink(self.path)

    def test_now_raw_is_deterministic_and_needs_no_backend(self):
        from cccopilot import observe as O
        st = S.build(T.parse(self.path))
        expected = O.next_step(self.path, st, SC.SESSION, sessions="")
        self.assertEqual(API.Copilot().now(session=self.path, raw=True), expected)

    def test_ask_wires_context_question_and_backend(self):
        out = API.Copilot().ask(session=self.path, question="did it drift?",
                                 backend=self.be)
        self.assertEqual(out, "ok")
        self.assertEqual(len(self.be.prompts), 1)
        # the composed prompt carries the question and the evidence context
        self.assertIn("did it drift?", self.be.prompts[0])
        self.assertIn("EVIDENCE CONTEXT", self.be.prompts[0])

    def test_chat_wires_history_question_and_backend(self):
        out = API.Copilot().chat(session=self.path,
                                 history=[("user", "what next?"),
                                          ("assistant", "check status")],
                                 question="and now?", backend=self.be)
        self.assertEqual(out, "ok")
        self.assertIn("and now?", self.be.prompts[0])
        # prior turns are replayed into the prompt
        self.assertIn("check status", self.be.prompts[0])

    def test_narrate_brief_wires_brief_and_backend(self):
        out = API.Copilot().narrate_brief(session=self.path, backend=self.be)
        self.assertEqual(out, "ok")
        self.assertIn("EVIDENCE CONTEXT", self.be.prompts[0])

    def test_now_llm_falls_back_to_deterministic_on_backend_error(self):
        class Boom(_CaptureBackend):
            def complete(self, prompt, model=None, timeout=180):
                raise RuntimeError("backend exploded")
        from cccopilot import observe as O
        st = S.build(T.parse(self.path))
        expected = O.next_step(self.path, st, SC.SESSION, sessions="")
        out = API.Copilot().now(session=self.path, backend=Boom())
        self.assertEqual(out, expected)

    def test_ask_stream_returns_handle_that_drains_to_text(self):
        h = API.Copilot().ask_stream(session=self.path, question="did it drift?",
                                      backend=self.be)
        self.assertIsInstance(h, StreamHandle)
        chunks = list(h)
        self.assertTrue(h.done)
        self.assertEqual(h.text, "ok")
        self.assertEqual(chunks, ["ok"])

    def test_chat_stream_and_now_stream_drain(self):
        cp = API.Copilot()
        h1 = cp.chat_stream(session=self.path, history=[], question="go?",
                             backend=self.be)
        self.assertEqual(list(h1), ["ok"])
        self.assertEqual(h1.text, "ok")
        h2 = cp.now_stream(session=self.path, backend=self.be)
        self.assertEqual(list(h2), ["ok"])
        self.assertEqual(h2.text, "ok")

    def test_goal_raw_is_deterministic(self):
        from cccopilot import chat as C
        st = S.build(T.parse(self.path))
        expected = C._deterministic_goal(st, "")
        self.assertEqual(API.Copilot().goal(session=self.path, raw=True), expected)

    def test_goal_llm_composes_rec_and_fallback(self):
        out = API.Copilot().goal(session=self.path, backend=self.be)
        self.assertIn("ok", out)                       # the LLM rec
        self.assertIn("/goal", out)                    # deterministic fallback command
        self.assertEqual(len(self.be.prompts), 1)

    def test_loop_raw_is_deterministic(self):
        from cccopilot import chat as C
        st = S.build(T.parse(self.path))
        expected = C._deterministic_loop(st, "")
        self.assertEqual(API.Copilot().loop(session=self.path, raw=True), expected)

    def test_loop_llm_composes_rec_and_fallback(self):
        out = API.Copilot().loop(session=self.path, backend=self.be)
        self.assertIn("ok", out)
        self.assertIn("/loop", out)

    def test_goal_stream_and_loop_stream_drain(self):
        cp = API.Copilot()
        h1 = cp.goal_stream(session=self.path, backend=self.be)
        self.assertEqual(list(h1), ["ok"])
        h2 = cp.loop_stream(session=self.path, backend=self.be)
        self.assertEqual(list(h2), ["ok"])

    def test_recap_since_duration_narrates_delta(self):
        # a duration window yields a delta (no last-look mark needed), which
        # recap_since narrates via the backend.
        out = API.Copilot().recap_since(session=self.path, when="30m",
                                         backend=self.be)
        self.assertEqual(out, "ok")
        self.assertEqual(len(self.be.prompts), 1)

    def test_recap_since_no_mark_does_not_narrate(self):
        # with no last-look mark and last-look tracking off, since returns the
        # status message; recap_since must not call the backend.
        from cccopilot import lastlook as LL
        saved = os.environ.get("CC_COPILOT_HISTORY")
        os.environ["CC_COPILOT_HISTORY"] = "0"   # tracking off -> "tracking is off"
        try:
            out = API.Copilot().recap_since(session=self.path, backend=self.be)
            self.assertEqual(self.be.prompts, [])   # no narration
            self.assertIn("tracking is off", out)
        finally:
            if saved is None:
                os.environ.pop("CC_COPILOT_HISTORY", None)
            else:
                os.environ["CC_COPILOT_HISTORY"] = saved

    def test_handoff_renders_shareable_brief(self):
        out = API.Copilot().handoff(session=self.path)
        self.assertIn("# Handoff", out)
        self.assertIn("Full brief", out)
        self.assertIn("testsess", out)  # the session id appears in the meta


# ---- cockpit session persistence ----------------------------------------

class TestCockpitPersistence(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY")}
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="ccapi-cockpit-")
        os.environ.pop("CC_COPILOT_HISTORY", None)
        self.path = write(_FIXTURE)
        self.cp = API.Copilot()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.unlink(self.path)

    def test_record_history_forget_roundtrip(self):
        self.assertEqual(self.cp.cockpit_history(session=self.path), [])
        n = self.cp.cockpit_record(session=self.path, question="hi", answer="hello back")
        self.assertEqual(n, 1)
        self.assertEqual(self.cp.cockpit_history(session=self.path),
                         [["user", "hi"], ["assistant", "hello back"]])
        self.assertTrue(self.cp.cockpit_forget(session=self.path))
        self.assertEqual(self.cp.cockpit_history(session=self.path), [])

    def test_disabled_is_noop(self):
        os.environ["CC_COPILOT_HISTORY"] = "0"
        self.assertEqual(self.cp.cockpit_record(session=self.path, question="x", answer="y"), 0)
        self.assertEqual(self.cp.cockpit_history(session=self.path), [])
        self.assertFalse(self.cp.cockpit_forget(session=self.path))

    def test_sessions_lists_recorded(self):
        self.cp.cockpit_record(session=self.path, question="q1", answer="a1")
        sessions = self.cp.cockpit_sessions()
        self.assertTrue(any(os.path.abspath(self.path) == s.get("transcript")
                            for s in sessions))


if __name__ == "__main__":
    unittest.main()