"""Streaming responses + exact token usage (backends → narrate → chat).

Covers the pure stream parsers (claude stream-json, codex --json, SSE), the
CliBackend/OpenAICompatBackend streaming paths against fake processes and a
local HTTP server, the narrate StreamHandle contract, and ChatSession's
finalize-only-on-success rule. No real LLM is called.
"""

import http.server
import json
import os
import sys
import tempfile
import textwrap
import threading
import unittest

from cccopilot import backends as BK, chat as C, context as EC, narrate as N
from tests.util import write, user, asst


# ── pure parsers ──────────────────────────────────────────────────────────

def _j(obj):
    return json.dumps(obj)


class TestClaudeParser(unittest.TestCase):
    def _events(self, lines):
        return list(BK._parse_claude_stream(iter(lines)))

    def test_deltas_then_usage(self):
        lines = [
            _j({"type": "system", "subtype": "init"}),
            _j({"type": "stream_event",
                "event": {"type": "content_block_delta",
                          "delta": {"type": "text_delta", "text": "hel"}}}),
            _j({"type": "stream_event",
                "event": {"type": "content_block_delta",
                          "delta": {"type": "text_delta", "text": "lo"}}}),
            _j({"type": "assistant",
                "message": {"content": [{"type": "text", "text": "hello"}]}}),
            _j({"type": "result", "subtype": "success", "is_error": False,
                "result": "hello", "total_cost_usd": 0.12,
                "usage": {"input_tokens": 100, "output_tokens": 4,
                          "cache_read_input_tokens": 30,
                          "cache_creation_input_tokens": 70}}),
        ]
        ev = self._events(lines)
        texts = [v for k, v in ev if k == "text"]
        self.assertEqual(texts, ["hel", "lo"])          # assistant event NOT doubled
        usage = [v for k, v in ev if k == "usage"][0]
        self.assertEqual(usage.input_tokens, 200)       # input + cache read + creation
        self.assertEqual(usage.output_tokens, 4)
        self.assertEqual(usage.cached_tokens, 30)
        self.assertEqual(usage.cost_usd, 0.12)
        self.assertTrue(usage.exact)

    def test_no_deltas_falls_back_to_assistant_message(self):
        lines = [
            _j({"type": "assistant",
                "message": {"content": [{"type": "text", "text": "whole answer"}]}}),
            _j({"type": "result", "subtype": "success", "is_error": False,
                "result": "whole answer", "usage": {"input_tokens": 5, "output_tokens": 2}}),
        ]
        ev = self._events(lines)
        self.assertEqual([v for k, v in ev if k == "text"], ["whole answer"])

    def test_result_only_fallback(self):
        ev = self._events([_j({"type": "result", "subtype": "success",
                               "is_error": False, "result": "just result"})])
        self.assertEqual([v for k, v in ev if k == "text"], ["just result"])

    def test_is_error_raises(self):
        lines = [_j({"type": "result", "subtype": "error_during_execution",
                     "is_error": True, "result": "boom"})]
        with self.assertRaises(BK.BackendError):
            self._events(lines)

    def test_non_json_noise_skipped(self):
        ev = self._events(["not json at all",
                           _j({"type": "result", "is_error": False, "result": "x"})])
        self.assertEqual([v for k, v in ev if k == "text"], ["x"])


class TestCodexParser(unittest.TestCase):
    def test_messages_and_usage(self):
        lines = [
            _j({"type": "thread.started", "thread_id": "t1"}),
            _j({"type": "turn.started"}),
            _j({"type": "item.completed", "item": {"type": "agent_message", "text": "part one"}}),
            _j({"type": "item.completed", "item": {"type": "reasoning", "text": "skip me"}}),
            _j({"type": "item.completed", "item": {"type": "agent_message", "text": "part two"}}),
            _j({"type": "turn.completed",
                "usage": {"input_tokens": 17029, "cached_input_tokens": 11648,
                          "output_tokens": 23}}),
        ]
        ev = list(BK._parse_codex_stream(iter(lines)))
        self.assertEqual([v for k, v in ev if k == "text"], ["part one", "\n\npart two"])
        usage = [v for k, v in ev if k == "usage"][0]
        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.cached_tokens),
                         (17029, 23, 11648))
        self.assertIsNone(usage.cost_usd)               # codex reports no cost

    def test_turn_failed_raises(self):
        lines = [_j({"type": "turn.failed", "error": {"message": "rate limited"}})]
        with self.assertRaises(BK.BackendError) as cm:
            list(BK._parse_codex_stream(iter(lines)))
        self.assertIn("rate limited", str(cm.exception))


class TestSseParser(unittest.TestCase):
    def test_chunks_usage_and_done(self):
        lines = [
            "data: " + _j({"choices": [{"delta": {"content": "he"}}]}),
            "",
            "data: " + _j({"choices": [{"delta": {"content": "llo"}}]}),
            "data: " + _j({"choices": [],
                           "usage": {"prompt_tokens": 9, "completion_tokens": 2,
                                     "prompt_tokens_details": {"cached_tokens": 3}}}),
            "data: [DONE]",
        ]
        ev = list(BK._parse_sse_stream(iter(lines)))
        self.assertEqual([v for k, v in ev if k == "text"], ["he", "llo"])
        usage = [v for k, v in ev if k == "usage"][0]
        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.cached_tokens),
                         (9, 2, 3))

    def test_plain_json_body_despite_stream_true(self):
        body = _j({"choices": [{"message": {"content": "blocking shape"}}],
                   "usage": {"prompt_tokens": 4, "completion_tokens": 1}})
        ev = list(BK._parse_sse_stream(iter(body.splitlines())))
        self.assertEqual([v for k, v in ev if k == "text"], ["blocking shape"])
        self.assertEqual([v for k, v in ev if k == "usage"][0].input_tokens, 4)


# ── CliBackend.stream against fake CLIs ───────────────────────────────────

_FAKE_CLAUDE = textwrap.dedent("""\
    import json, sys, time
    if "--help" in sys.argv:
        print("--output-format stream-json --verbose --include-partial-messages")
        sys.exit(0)
    for t in ("str", "eam"):
        print(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": t}}}), flush=True)
        time.sleep(0.05)
    print(json.dumps({"type": "result", "is_error": False, "result": "stream",
                      "total_cost_usd": 0.01,
                      "usage": {"input_tokens": 11, "output_tokens": 2}}), flush=True)
""")

_FAKE_CODEX = textwrap.dedent("""\
    import json, sys
    if "--help" in sys.argv:
        print("--json   Print events to stdout as JSONL")
        sys.exit(0)
    print(json.dumps({"type": "item.completed",
                      "item": {"type": "agent_message", "text": "codex says"}}), flush=True)
    print(json.dumps({"type": "turn.completed",
                      "usage": {"input_tokens": 7, "cached_input_tokens": 2,
                                "output_tokens": 3}}), flush=True)
""")


class _FakeCli(unittest.TestCase):
    def _script(self, body):
        fd, path = tempfile.mkstemp(suffix=".py", prefix="ccfake-")
        with os.fdopen(fd, "w") as f:
            f.write(body)
        self.addCleanup(os.unlink, path)
        BK._HELP_CACHE.clear()
        self.addCleanup(BK._HELP_CACHE.clear)
        return path


class TestCliStream(_FakeCli):
    def test_claude_flavor_streams_and_reports_usage(self):
        script = self._script(_FAKE_CLAUDE)
        be = BK.CliBackend("claude", [sys.executable, script, "-p"], flavor="claude")
        chunks = list(be.stream("q"))
        self.assertEqual(chunks, ["str", "eam"])
        self.assertEqual(be.last_usage.output_tokens, 2)
        self.assertEqual(be.last_usage.cost_usd, 0.01)

    def test_codex_flavor_streams_and_reports_usage(self):
        script = self._script(_FAKE_CODEX)
        be = BK.CliBackend("codex", [sys.executable, script, "exec"], flavor="codex")
        chunks = list(be.stream("q"))
        self.assertEqual(chunks, ["codex says"])
        self.assertEqual(be.last_usage.input_tokens, 7)

    def test_no_flavor_falls_back_to_single_chunk(self):
        script = self._script("import sys\n"
                              "sys.exit(0) if '--help' in sys.argv else print('plain answer')\n")
        be = BK.CliBackend("llm", [sys.executable, script])
        self.assertEqual(list(be.stream("q")), ["plain answer"])
        self.assertIsNone(be.last_usage)

    def test_unsupported_flags_fall_back_to_blocking(self):
        # --help advertises NO stream flags → stream() must use complete()
        script = self._script("import sys\n"
                              "print('no fancy flags here') if '--help' in sys.argv "
                              "else print('blocking answer')\n")
        be = BK.CliBackend("claude", [sys.executable, script, "-p"], flavor="claude")
        self.assertEqual(list(be.stream("q")), ["blocking answer"])

    def test_nonzero_exit_without_output_raises_stderr(self):
        script = self._script("import sys\n"
                              "(print('--output-format stream-json'), sys.exit(0)) "
                              "if '--help' in sys.argv else None\n"
                              "sys.stderr.write('exploded')\n"
                              "sys.exit(3)\n")
        be = BK.CliBackend("claude", [sys.executable, script, "-p"], flavor="claude")
        with self.assertRaises(BK.BackendError) as cm:
            list(be.stream("q"))
        self.assertIn("exploded", str(cm.exception))

    def test_empty_stream_raises(self):
        script = self._script("import sys\n"
                              "print('--output-format stream-json') "
                              "if '--help' in sys.argv else None\n")
        be = BK.CliBackend("claude", [sys.executable, script, "-p"], flavor="claude")
        with self.assertRaises(BK.BackendError) as cm:
            list(be.stream("q"))
        self.assertIn("no output", str(cm.exception))

    def test_timeout_kills_and_raises(self):
        script = self._script("import sys, time\n"
                              "(print('--output-format stream-json'), sys.exit(0)) "
                              "if '--help' in sys.argv else time.sleep(10)\n")
        be = BK.CliBackend("claude", [sys.executable, script, "-p"], flavor="claude")
        with self.assertRaises(BK.BackendError) as cm:
            list(be.stream("q", timeout=1))
        self.assertIn("timed out", str(cm.exception))


# ── OpenAICompatBackend.stream against a local HTTP server ───────────────

class _SseHandler(http.server.BaseHTTPRequestHandler):
    reject_stream_options = False
    saw_bodies = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        type(self).saw_bodies.append(body)
        if type(self).reject_stream_options and "stream_options" in body:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "stream_options not supported"}')
            return
        out = (
            "data: " + _j({"choices": [{"delta": {"content": "ss"}}]}) + "\n\n"
            "data: " + _j({"choices": [{"delta": {"content": "e!"}}]}) + "\n\n"
            "data: " + _j({"choices": [],
                           "usage": {"prompt_tokens": 6, "completion_tokens": 2}}) + "\n\n"
            "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class TestHttpStream(unittest.TestCase):
    def _serve(self, handler):
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}/chat/completions"

    def test_sse_stream_with_usage(self):
        _SseHandler.reject_stream_options = False
        _SseHandler.saw_bodies = []
        be = BK.OpenAICompatBackend("t", self._serve(_SseHandler), "NOKEY", "m",
                                    needs_key=False)
        chunks = list(be.stream("hi"))
        self.assertEqual(chunks, ["ss", "e!"])
        self.assertEqual(be.last_usage.input_tokens, 6)
        self.assertTrue(be.last_usage.exact)
        self.assertTrue(_SseHandler.saw_bodies[0].get("stream"))
        self.assertIn("stream_options", _SseHandler.saw_bodies[0])

    def test_stream_options_rejected_retries_without(self):
        _SseHandler.reject_stream_options = True
        _SseHandler.saw_bodies = []
        be = BK.OpenAICompatBackend("t", self._serve(_SseHandler), "NOKEY", "m",
                                    needs_key=False)
        chunks = list(be.stream("hi"))
        self.assertEqual(chunks, ["ss", "e!"])
        self.assertEqual(len(_SseHandler.saw_bodies), 2)
        self.assertNotIn("stream_options", _SseHandler.saw_bodies[1])

    def test_streaming_unsupported_falls_back_to_blocking(self):
        # a provider that 400s ANY stream:true body must degrade to the
        # blocking complete() path (it worked before streaming existed)
        class H(http.server.BaseHTTPRequestHandler):
            saw_bodies = []

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                type(self).saw_bodies.append(body)
                if body.get("stream"):
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error": "streaming not supported"}')
                    return
                out = _j({"choices": [{"message": {"content": "blocking works"}}],
                          "usage": {"prompt_tokens": 5, "completion_tokens": 2}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        H.saw_bodies = []
        be = BK.OpenAICompatBackend("t", self._serve(H), "NOKEY", "m", needs_key=False)
        chunks = list(be.stream("hi"))
        self.assertEqual(chunks, ["blocking works"])
        self.assertEqual(len(H.saw_bodies), 3)           # stream+opts, stream, blocking
        self.assertFalse(H.saw_bodies[2].get("stream"))
        self.assertEqual(be.last_usage.input_tokens, 5)  # usage still captured

    def test_auth_error_raises_immediately_no_blocking_fallback(self):
        # a 401 must surface as-is — NOT trigger the degrade ladder
        class H(http.server.BaseHTTPRequestHandler):
            calls = 0

            def log_message(self, *a):
                pass

            def do_POST(self):
                type(self).calls += 1
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error": "bad key"}')

        H.calls = 0
        be = BK.OpenAICompatBackend("t", self._serve(H), "NOKEY", "m", needs_key=False)
        with self.assertRaises(BK.BackendError) as cm:
            list(be.stream("hi"))
        self.assertIn("401", str(cm.exception))
        self.assertEqual(H.calls, 1)                     # no retries on auth errors

    def test_blocking_complete_captures_usage(self):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                out = _j({"choices": [{"message": {"content": "full"}}],
                          "usage": {"prompt_tokens": 8, "completion_tokens": 3}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        be = BK.OpenAICompatBackend("t", self._serve(H), "NOKEY", "m", needs_key=False)
        self.assertEqual(be.complete("hi"), "full")
        self.assertEqual((be.last_usage.input_tokens, be.last_usage.output_tokens), (8, 3))


# ── narrate.StreamHandle + funnels ────────────────────────────────────────

class _StubBackend(BK.Backend):
    name = "stub"

    def __init__(self, chunks=("a", "b"), usage=None, fail_after=None):
        self.chunks = list(chunks)
        self.usage = usage
        self.fail_after = fail_after
        self.complete_calls = 0
        self.stream_calls = 0
        self.prompts = []

    def available(self):
        return True

    def complete(self, prompt, model=None, timeout=180):
        self.complete_calls += 1
        self.prompts.append(prompt)
        self.last_usage = self.usage
        return "".join(self.chunks)

    def stream(self, prompt, model=None, timeout=180):
        self.stream_calls += 1
        self.prompts.append(prompt)
        self.last_usage = None
        for i, c in enumerate(self.chunks):
            if self.fail_after is not None and i >= self.fail_after:
                raise BK.BackendError("mid-stream failure")
            yield c
        self.last_usage = self.usage


class TestStreamHandle(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("CC_COPILOT_STREAM", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["CC_COPILOT_STREAM"] = self._saved
        else:
            os.environ.pop("CC_COPILOT_STREAM", None)

    def test_chunks_text_and_usage(self):
        be = _StubBackend(chunks=("hel", "lo "), usage=BK.Usage(10, 5))
        h = N.run_brief_stream("brief", "task", backend=be)
        self.assertEqual(list(h), ["hel", "lo "])
        self.assertEqual(h.text, "hello")               # stripped accumulation
        self.assertEqual(h.usage.output_tokens, 5)
        self.assertTrue(h.done)
        self.assertEqual(be.stream_calls, 1)
        self.assertEqual(be.complete_calls, 0)

    def test_partial_text_kept_on_midstream_error(self):
        be = _StubBackend(chunks=("par", "tial"), fail_after=1)
        h = N.run_brief_stream("brief", "task", backend=be)
        out = []
        with self.assertRaises(BK.BackendError):
            for c in h:
                out.append(c)
        self.assertEqual(out, ["par"])
        self.assertEqual(h.text, "par")                 # what the user saw
        self.assertTrue(h.done)

    def test_opt_out_env_forces_blocking(self):
        os.environ["CC_COPILOT_STREAM"] = "0"
        be = _StubBackend(chunks=("x", "y"))
        h = N.run_brief_stream("brief", "task", backend=be)
        self.assertEqual(list(h), ["xy"])               # one blocking chunk
        self.assertEqual(be.complete_calls, 1)
        self.assertEqual(be.stream_calls, 0)

    def test_construction_is_lazy(self):
        be = _StubBackend()
        h = N.chat_brief_stream("brief", [], "the question?", backend=be)
        self.assertEqual(be.prompts, [])                # nothing ran yet
        list(h)
        self.assertEqual(len(be.prompts), 1)
        self.assertIn("the question?", be.prompts[0])
        self.assertIn("EVIDENCE CONTEXT", be.prompts[0])

    def test_unavailable_backend_raises_at_call(self):
        class Off(BK.Backend):
            name = "off"

            def reason(self):
                return "nope"

        self.assertRaises(RuntimeError, N.run_brief_stream, "b", "t", backend=Off())


# ── ChatSession.answer_stream finalize rules ──────────────────────────────

class TestAnswerStream(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ccstream-")
        self._env = {k: os.environ.get(k) for k in
                     ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY", "CC_COPILOT_CONFIG")}
        os.environ["CC_COPILOT_STATE_DIR"] = self.home
        os.environ["CC_COPILOT_HISTORY"] = "1"
        os.environ["CC_COPILOT_CONFIG"] = os.path.join(self.home, "none.toml")
        self._real = N.chat_brief_stream

    def tearDown(self):
        N.chat_brief_stream = self._real
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _session(self):
        path = write([user("task", 100, sessionId="sess-stream", cwd="/test/proj"),
                      asst("ok", 50)])
        return C.ChatSession(path, backend="codex", alerts=False)

    def test_success_finalizes_with_exact_usage(self):
        be = _StubBackend(chunks=("answer ", "text"), usage=BK.Usage(100, 42))
        N.chat_brief_stream = (lambda brief, hist, q, model=None, backend=None:
                               N.run_brief_stream(brief, q, backend=be))
        s = self._session()
        chunks = list(s.answer_stream("what happened?"))
        self.assertEqual("".join(chunks), "answer text")
        self.assertEqual(s.history[-1], ("assistant", "answer text"))
        self.assertEqual(s.last_output_tokens, 42)      # exact, not chars/4
        turns = s.store._load_turns()
        self.assertEqual(turns[-1]["a"], "answer text")
        self.assertEqual(turns[-1]["usage"]["output_tokens"], 42)

    def test_midstream_error_persists_nothing(self):
        be = _StubBackend(chunks=("doomed", "x"), fail_after=1)
        N.chat_brief_stream = (lambda brief, hist, q, model=None, backend=None:
                               N.run_brief_stream(brief, q, backend=be))
        s = self._session()
        with self.assertRaises(BK.BackendError):
            list(s.answer_stream("q?"))
        self.assertEqual(s.history, [])                 # nothing in memory
        self.assertEqual(s.store._load_turns(), [])     # nothing durable


# ── HUD formatters ────────────────────────────────────────────────────────

class TestHudExactness(unittest.TestCase):
    def test_format_hud_estimate_vs_exact(self):
        st = EC.ContextStats(estimated_tokens=1000, budget_tokens=60000)
        self.assertIn("out ~50", EC.format_hud(st, 50))
        self.assertIn("out 50", EC.format_hud(st, 50, out_exact=True))
        self.assertNotIn("out ~50", EC.format_hud(st, 50, out_exact=True))

    def test_format_hud_cost(self):
        st = EC.ContextStats()
        self.assertIn("$0.34", EC.format_hud(st, 10, cost_usd=0.344))
        self.assertIn("$<0.01", EC.format_hud(st, 10, cost_usd=0.001))
        self.assertNotIn("$", EC.format_hud(st, 10))

    def test_format_answering_exact(self):
        st = EC.ContextStats(estimated_tokens=100, raw_tokens=50)
        self.assertIn("out ~7", EC.format_answering(st, 7))
        self.assertIn("out 7", EC.format_answering(st, 7, out_exact=True))


if __name__ == "__main__":
    unittest.main()
