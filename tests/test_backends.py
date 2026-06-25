import http.server
import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from cccopilot import backends as BK, cli

_ENV = ("CC_COPILOT_BACKEND", "CC_COPILOT_LLM_CMD", "CC_COPILOT_API_BASE",
        "CC_COPILOT_API_KEY", "CC_COPILOT_MODEL")


class TestResolve(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in _ENV}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_default_is_codex(self):
        self.assertEqual(BK.resolve().name, "codex")

    def test_named(self):
        self.assertEqual(BK.resolve("deepseek").name, "deepseek")

    def test_env_backend_overrides_default(self):
        os.environ["CC_COPILOT_BACKEND"] = "claude"
        self.assertEqual(BK.resolve().name, "claude")

    def test_unknown_raises(self):
        self.assertRaises(BK.BackendError, BK.resolve, "nope")

    def test_api_base_makes_custom_default(self):
        os.environ["CC_COPILOT_API_BASE"] = "http://localhost:1234"
        self.assertEqual(BK.resolve().name, "custom")

    def test_named_default_beats_api_base(self):
        os.environ["CC_COPILOT_API_BASE"] = "http://localhost:1234"
        os.environ["CC_COPILOT_BACKEND"] = "claude"
        self.assertEqual(BK.resolve().name, "claude")

    def test_deepseek_needs_key(self):
        self.assertFalse(BK.registry()["deepseek"].available())

    def test_codex_argv_skips_git_check(self):
        self.assertIn("--skip-git-repo-check", BK.registry()["codex"].argv)

    def test_codex_argv_adds_read_only_safety_flags_when_supported(self):
        help_text = "--sandbox --ephemeral --ignore-rules --ignore-user-config"
        with mock.patch.object(BK, "_cli_help", return_value=help_text):
            argv = BK.registry()["codex"]._full_argv("prompt", None)
        self.assertIn("--skip-git-repo-check", argv)
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertIn("--ephemeral", argv)
        self.assertIn("--ignore-rules", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertEqual(argv[-1], "prompt")

    def test_claude_argv_disables_tools_when_supported(self):
        help_text = ("--tools --no-session-persistence --safe-mode --no-chrome "
                     "--strict-mcp-config --disable-slash-commands")
        with mock.patch.object(BK, "_cli_help", return_value=help_text):
            argv = BK.registry()["claude"]._full_argv("prompt", "sonnet")
        self.assertIn("--tools", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", argv)
        self.assertIn("--safe-mode", argv)
        self.assertIn("--no-chrome", argv)
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--disable-slash-commands", argv)
        self.assertIn("--model", argv)
        self.assertEqual(argv[-1], "prompt")

    def test_custom_cli_command_uses_shell_like_quoting(self):
        os.environ["CC_COPILOT_LLM_CMD"] = 'fake-llm --system "read only"'
        be = BK.resolve()
        self.assertEqual(be.argv, ["fake-llm", "--system", "read only"])
        self.assertEqual(be.cwd, tempfile.gettempdir())

    def test_custom_cli_unbalanced_quote_raises_backend_error(self):
        # shlex.split raises ValueError on unbalanced quotes; resolve() must
        # surface that as a BackendError so the callers that already guard on
        # BackendError (narration, `backends`) degrade instead of tracebacking.
        os.environ["CC_COPILOT_LLM_CMD"] = 'my-llm --opt "broken'
        with self.assertRaises(BK.BackendError):
            BK.resolve()

    def test_optional_safety_flags_omitted_but_readonly_flag_still_applied(self):
        # An older CLI that advertises the load-bearing read-only flag but lacks
        # the defense-in-depth extras still runs — with the read-only flag, and
        # without the absent extras (which the CLI would reject).
        claude_help = "usage: claude -p [--tools T] [--model M] [prompt]"
        with mock.patch.object(BK, "_cli_help", return_value=claude_help):
            argv = BK.registry()["claude"]._full_argv("prompt", "sonnet")
        self.assertIn("--tools", argv)                    # load-bearing, applied
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        for flag in ("--no-session-persistence", "--safe-mode", "--no-chrome",
                     "--strict-mcp-config", "--disable-slash-commands"):
            self.assertNotIn(flag, argv)                  # absent extras skipped
        self.assertIn("--model", argv)
        self.assertEqual(argv[-1], "prompt")

    def test_fail_closed_when_cli_lacks_readonly_flag(self):
        # The security fix: a CLI whose help positively does NOT advertise the
        # load-bearing read-only flag must be REFUSED, not launched unguarded.
        claude_help = "usage: claude -p [--model M] [prompt]"   # no --tools
        with mock.patch.object(BK, "_cli_help", return_value=claude_help):
            with self.assertRaises(BK.BackendError):
                BK.registry()["claude"]._full_argv("prompt", "sonnet")

        codex_help = "usage: codex exec [--model M]"            # no --sandbox
        with mock.patch.object(BK, "_cli_help", return_value=codex_help):
            with self.assertRaises(BK.BackendError):
                BK.registry()["codex"]._full_argv("prompt", None)

    def test_unprobeable_cli_still_gets_readonly_flag_best_effort(self):
        # If --help can't be captured at all (empty), we can't confirm absence,
        # so we don't hard-fail — but we DO still apply the read-only flag, so a
        # CLI is never launched as a free agent. A CLI that truly lacks it will
        # reject the flag loudly (still fail-closed) rather than run unconfined.
        with mock.patch.object(BK, "_cli_help", return_value=""):
            argv = BK.registry()["claude"]._full_argv("prompt", "sonnet")
            self.assertIn("--tools", argv)
            self.assertEqual(argv[argv.index("--tools") + 1], "")
            cargv = BK.registry()["codex"]._full_argv("prompt", None)
            self.assertIn("--sandbox", cargv)
            self.assertEqual(cargv[cargv.index("--sandbox") + 1], "read-only")

    def test_narrator_backends_are_constructed_with_safety_args(self):
        # No narrator agent CLI may exist in the registry without its safety gate.
        reg = BK.registry()
        for name in ("claude", "codex"):
            self.assertIsNotNone(reg[name].safety_args,
                                 f"{name} backend missing safety_args")

    def test_cli_backends_run_from_neutral_tempdir(self):
        reg = BK.registry()
        for name in ("claude", "codex", "gemini", "llm"):
            self.assertEqual(reg[name].cwd, tempfile.gettempdir())

    def test_flag_supported_rejects_superstring_flags(self):
        # A help text that advertises only a longer flag must not enable the
        # shorter one (else the narrator gets launched with a rejected flag).
        self.assertTrue(BK._flag_supported("--tools <tools...>  disable", "--tools"))
        self.assertFalse(BK._flag_supported("only --tools-config here", "--tools"))
        self.assertTrue(BK._flag_supported("-s, --sandbox <MODE>", "--sandbox"))
        self.assertFalse(BK._flag_supported("--sandbox-mode foo", "--sandbox"))
        self.assertFalse(BK._flag_supported("--allowed-tools x", "--tools"))


class TestBackendsCommand(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in _ENV}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_invalid_active_backend_lists_choices_without_traceback(self):
        os.environ["CC_COPILOT_BACKEND"] = "does-not-exist"
        args = cli.build_parser().parse_args(["backends"])
        with mock.patch.object(BK.OpenAICompatBackend, "endpoint_health",
                               return_value=(False, "endpoint unreachable")), \
             mock.patch("sys.stdout") as out, mock.patch("sys.stderr") as err:
            rc = cli.cmd_backends(args)
        self.assertEqual(rc, 2)
        stdout = "".join(call.args[0] for call in out.write.call_args_list)
        stderr = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertIn("LLM backends", stdout)
        self.assertIn("active: unknown backend 'does-not-exist'", stdout)
        self.assertIn("unknown backend 'does-not-exist'", stderr)

    def test_no_key_http_backend_status_can_report_unreachable(self):
        args = cli.build_parser().parse_args(["backends"])
        with mock.patch.object(BK.OpenAICompatBackend, "endpoint_health",
                               return_value=(False, "endpoint unreachable")), \
             mock.patch("sys.stdout") as out:
            rc = cli.cmd_backends(args)
        self.assertEqual(rc, 0)
        stdout = "".join(call.args[0] for call in out.write.call_args_list)
        self.assertIn("ollama", stdout)
        self.assertIn("needs: endpoint unreachable", stdout)


class TestOpenAICompat(unittest.TestCase):
    def test_http_roundtrip_with_model_override(self):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                out = {"choices": [{"message": {"content": "model=" + body["model"]}}]}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(out).encode())

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            be = BK.OpenAICompatBackend("t", f"http://127.0.0.1:{port}/chat/completions",
                                        "NOKEY", "default-m", needs_key=False)
            out = be.complete("hello", model="override-m")
            self.assertIn("model=override-m", out)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_openai_backend_adds_prompt_cache_key(self):
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                seen.update(json.loads(self.rfile.read(n)))
                out = {"choices": [{"message": {"content": "ok"}}],
                       "usage": {"prompt_tokens": 20, "completion_tokens": 2,
                                 "prompt_tokens_details": {"cached_tokens": 8}}}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(out).encode())

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            be = BK.OpenAICompatBackend("openai", f"http://127.0.0.1:{port}/chat/completions",
                                        "NOKEY", "gpt-test", needs_key=False)
            self.assertEqual(be.complete("hello"), "ok")
            self.assertEqual(seen["prompt_cache_key"], "cc-copilot:gpt-test")
            self.assertEqual(be.last_usage.cached_tokens, 8)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_non_openai_backend_does_not_send_prompt_cache_key(self):
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                seen.update(json.loads(self.rfile.read(n)))
                out = {"choices": [{"message": {"content": "ok"}}]}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(out).encode())

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            be = BK.OpenAICompatBackend("deepseek", f"http://127.0.0.1:{port}/chat/completions",
                                        "NOKEY", "deepseek-test", needs_key=False)
            self.assertEqual(be.complete("hello"), "ok")
            self.assertNotIn("prompt_cache_key", seen)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_missing_key_raises(self):
        be = BK.OpenAICompatBackend("t", "http://x/chat/completions", "DEFINITELY_UNSET_KEY", "m")
        self.assertFalse(be.available())
        self.assertRaises(BK.BackendError, be.complete, "hi")

    def test_ollama_cloud_backend(self):
        be = BK.registry()["ollama-cloud"]
        self.assertIsInstance(be, BK.OpenAICompatBackend)
        self.assertEqual(be.endpoint, "https://ollama.com/v1/chat/completions")
        self.assertEqual(be.key_env, "OLLAMA_API_KEY")
        self.assertTrue(be.needs_key)

    def test_no_key_endpoint_health_reports_connection_failure(self):
        be = BK.OpenAICompatBackend("t", "http://127.0.0.1:9/v1/chat/completions",
                                    "NOKEY", "m", needs_key=False)
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("refused")):
            ok, why = be.endpoint_health()
        self.assertFalse(ok)
        self.assertIn("endpoint unreachable", why)

    def test_endpoint_health_treats_http_response_as_reachable(self):
        be = BK.OpenAICompatBackend("t", "http://127.0.0.1/v1/chat/completions",
                                    "NOKEY", "m", needs_key=False)
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=urllib.error.HTTPError(
                                   be.endpoint, 404, "not found", {}, None)):
            ok, why = be.endpoint_health()
        self.assertTrue(ok)
        self.assertEqual(why, "endpoint reachable")

    def test_endpoint_health_timeout_is_unreachable(self):
        be = BK.OpenAICompatBackend("t", "http://127.0.0.1/v1/chat/completions",
                                    "NOKEY", "m", needs_key=False)
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=TimeoutError("slow")):
            ok, why = be.endpoint_health()
        self.assertFalse(ok)
        self.assertIn("endpoint unreachable", why)

    def test_endpoint_health_http_exception_does_not_escape(self):
        # A port that accepts TCP but speaks no HTTP raises BadStatusLine, which
        # is NOT an OSError — it must be caught, not crash `cc-copilot backends`.
        be = BK.OpenAICompatBackend("t", "http://127.0.0.1/v1/chat/completions",
                                    "NOKEY", "m", needs_key=False)
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=http.client.BadStatusLine("garbage")):
            ok, why = be.endpoint_health()
        self.assertFalse(ok)
        self.assertIn("endpoint unreachable", why)

    def test_endpoint_health_rejects_non_http_scheme(self):
        be = BK.OpenAICompatBackend("t", "file:///etc/passwd",
                                    "NOKEY", "m", needs_key=False)
        ok, why = be.endpoint_health()
        self.assertFalse(ok)
        self.assertEqual(why, "invalid endpoint")

    def test_endpoint_health_handles_malformed_url_without_crashing(self):
        # urlsplit() raises ValueError on an unterminated IPv6 literal before
        # any request is made — it must report invalid, not traceback.
        be = BK.OpenAICompatBackend("t", "http://[::1",
                                    "NOKEY", "m", needs_key=False)
        ok, why = be.endpoint_health()
        self.assertFalse(ok)
        self.assertEqual(why, "invalid endpoint")

    def test_null_content_raises_backend_error_not_attribute_error(self):
        # tool-call-only responses set choices[0].message.content to null; the
        # subsequent .strip() must surface as a clean BackendError.
        be = BK.OpenAICompatBackend("t", "http://x/v1/chat/completions", "K", "m")
        payload = json.dumps({"choices": [{"message": {"content": None}}]}).encode()

        class _Resp:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(be, "_key", return_value="k"), \
             mock.patch("urllib.request.urlopen", return_value=_Resp()):
            with self.assertRaises(BK.BackendError):
                be.complete("hi")


if __name__ == "__main__":
    unittest.main()
