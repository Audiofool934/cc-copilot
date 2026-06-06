import http.server
import json
import os
import threading
import unittest

from cccopilot import backends as BK

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

    def test_missing_key_raises(self):
        be = BK.OpenAICompatBackend("t", "http://x/chat/completions", "DEFINITELY_UNSET_KEY", "m")
        self.assertFalse(be.available())
        self.assertRaises(BK.BackendError, be.complete, "hi")


if __name__ == "__main__":
    unittest.main()
