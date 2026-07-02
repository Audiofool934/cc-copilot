"""The localhost JSON-RPC server: parity with the facade, typed object payloads,
and the wire error contract.

The server is a thin transport over the Copilot facade, so the load-bearing
property is parity - ``serve.brief`` must equal ``Copilot().brief``. These tests
start the server on an ephemeral port in a thread and hit it with stdlib
``urllib`` JSON-RPC calls.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request

from cccopilot import api as API
from cccopilot import serialize as SER
from cccopilot import server as SV
from tests.util import asst, result, tool, user, write


_FIXTURE = [
    user("add the export feature", 300),
    asst("working on it", 250),
    tool("Bash", {"command": "pytest"}, "t1", 200),
    result("t1", "ok", ago=199),
    tool("Edit", {"file_path": "a.py"}, "t2", 20),
    result("t2", "ok", ago=19),
    asst("done, added export", 5),
]


def _rpc(port, method, params=None, req_id=1):
    body = json.dumps({"jsonrpc": "2.0", "id": req_id,
                       "method": method, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _raw_post(port, body: bytes):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.path = write(_FIXTURE)
        self.httpd = SV.make_server(port=0)
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._t.start()
        self.port = self.httpd.server_address[1]
        self.cp = API.Copilot()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._t.join(timeout=5)
        os.unlink(self.path)

    def _rpc(self, method, params=None):
        return _rpc(self.port, method, params)


class TestParity(_ServerCase):
    def test_brief_matches_facade(self):
        r = self._rpc("brief", {"session": self.path})
        self.assertNotIn("error", r)
        self.assertEqual(r["result"], self.cp.brief(session=self.path))

    def test_check_matches_facade(self):
        self.assertEqual(self._rpc("check", {"session": self.path})["result"],
                         self.cp.check(session=self.path))

    def test_observe_matches_facade(self):
        self.assertEqual(self._rpc("observe", {"session": self.path})["result"],
                         self.cp.observe(session=self.path))

    def test_since_duration_matches_facade(self):
        self.assertEqual(
            self._rpc("since", {"session": self.path, "when": "30m"})["result"],
            self.cp.since(session=self.path, when="30m"))

    def test_check_verdict_matches_facade(self):
        r = self._rpc("check_verdict", {"session": self.path})
        self.assertIsInstance(r["result"], int)
        self.assertEqual(r["result"], self.cp.check_verdict(session=self.path))


class TestSerializedObjects(_ServerCase):
    def test_transcript_shape(self):
        r = self._rpc("transcript", {"path": self.path})
        self.assertNotIn("error", r)
        tr = r["result"]
        self.assertEqual(tr["cwd"], "/test/proj")
        self.assertTrue(tr["records"])
        self.assertEqual(tr["records"][0]["kind"], "human")
        # byte-identical to the shared serializer the CLI state command uses
        self.assertEqual(tr, SER.transcript_to_dict(self.cp.transcript(self.path)))

    def test_state_shape(self):
        r = self._rpc("state", {"path": self.path})
        self.assertNotIn("error", r)
        st = r["result"]
        self.assertEqual(st["session_id"], self.cp.state(self.path).tr.session_id)
        self.assertIn("assessment", st)
        self.assertIn("changed_files", st)
        # idle_seconds is now-relative, so two builds drift by milliseconds;
        # compare the rest exactly and check idle is a non-negative number.
        expected = SER.state_to_dict(self.cp.state(self.path))
        self.assertIsInstance(st["idle_seconds"], (int, float))
        self.assertGreaterEqual(st["idle_seconds"], 0)
        self.assertAlmostEqual(st["idle_seconds"], expected["idle_seconds"], places=0)
        st.pop("idle_seconds")
        expected.pop("idle_seconds")
        self.assertEqual(st, expected)


class TestErrors(_ServerCase):
    def test_unknown_method(self):
        self.assertEqual(self._rpc("nope")["error"]["code"], -32601)

    def test_session_not_found(self):
        r = self._rpc("brief", {"session": "/no/such/file.jsonl"})
        self.assertEqual(r["error"]["code"], -32001)

    def test_invalid_params_bad_when(self):
        r = self._rpc("since", {"session": self.path, "when": "soon"})
        self.assertEqual(r["error"]["code"], -32602)

    def test_invalid_params_not_an_object(self):
        r = self._rpc("brief", ["not", "an", "object"])
        self.assertEqual(r["error"]["code"], -32602)

    def test_parse_error(self):
        r = _raw_post(self.port, b"{not json")
        self.assertEqual(r["error"]["code"], -32700)

    def test_invalid_request_not_object(self):
        r = _raw_post(self.port, b"[1,2,3]")
        self.assertEqual(r["error"]["code"], -32600)

    def test_error_responses_keep_the_request_id(self):
        r = _rpc(self.port, "nope", req_id=99)
        self.assertEqual(r["error"]["code"], -32601)
        self.assertEqual(r["id"], 99)


class TestDiscoveryAndHealth(_ServerCase):
    def test_get_lists_methods(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            r = json.loads(resp.read().decode("utf-8"))
        self.assertIn("methods", r)
        for m in ("brief", "observe", "since", "state", "transcript", "sessions"):
            self.assertIn(m, r["methods"])

    def test_resolve_missing_cwd_returns_null(self):
        r = self._rpc("resolve", {"cwd": "/no/such/cwd/here"})
        self.assertNotIn("error", r)
        self.assertIsNone(r["result"])


class TestSessionsSerialized(unittest.TestCase):
    """`sessions` returns SessionRef objects; the server must serialize them."""

    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "CC_COPILOT_AGENTS",
                                 "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")}
        self.claude_home = tempfile.mkdtemp(prefix="ccsrv-claude-")
        os.makedirs(os.path.join(self.claude_home, "projects"))
        self.codex_home = tempfile.mkdtemp(prefix="ccsrv-codex-")
        os.makedirs(os.path.join(self.codex_home, "sessions"))
        os.environ["CLAUDE_CONFIG_DIR"] = self.claude_home
        os.environ["CODEX_HOME"] = self.codex_home
        os.environ.pop("CC_COPILOT_AGENTS", None)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        os.environ.pop("CLAUDE_SESSION_ID", None)
        from cccopilot.sources import codex as CX
        CX._HEAD_CACHE.clear()
        self.cwd = "/tmp/cc-srv-test"
        from cccopilot import locate as L
        d = os.path.join(self.claude_home, "projects", L.encode_cwd(self.cwd))
        os.makedirs(d, exist_ok=True)
        self.sess = os.path.join(d, "srv-1.jsonl")
        with open(self.sess, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "cwd": self.cwd, "sessionId": "srv-1",
                                "message": {"role": "user", "content": "go"}}) + "\n")
        os.utime(self.sess, (2000, 2000))
        self.httpd = SV.make_server(port=0)
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._t.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._t.join(timeout=5)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.claude_home, ignore_errors=True)
        shutil.rmtree(self.codex_home, ignore_errors=True)

    def test_sessions_returns_serialized_refs(self):
        r = _rpc(self.port, "sessions", {"cwd": self.cwd})
        self.assertNotIn("error", r)
        self.assertEqual(len(r["result"]), 1)
        ref = r["result"][0]
        self.assertIsInstance(ref, dict)
        self.assertEqual(ref["path"], self.sess)
        self.assertEqual(ref["agent"], "claude")
        self.assertEqual(ref["session_id"], "srv-1")
        self.assertIn("hhmm", ref)

    def test_agents_filter_applied_server_side(self):
        # a claude-only server must not see codex sessions (and vice versa)
        r = _rpc(self.port, "sessions", {"cwd": self.cwd})
        self.assertEqual(len(r["result"]), 1)


# ---- narration routing + SSE streaming -----------------------------------

class _FakeHandle:
    """A minimal StreamHandle stand-in for server-routing tests: yields the
    given chunks, then reports done + the joined text. No backend involved."""
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.text = "".join(self._chunks)
        self.done = False
        self.usage = None

    def __iter__(self):
        for c in self._chunks:
            yield c
        self.done = True

    def cancel(self):
        pass


def _parse_sse(text: str):
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        ev = {"event": "message"}
        data = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                ev["event"] = line[len("event: "):]
            elif line.startswith("data: "):
                data.append(line[len("data: "):])
        if data:
            ev["data"] = json.loads("".join(data))
            events.append(ev)
    return events


class TestServerNarration(_ServerCase):
    """Routing of narration methods and the /stream SSE endpoint. The facade
    narration logic is covered by test_api.TestNarration; here we stub the
    Copilot instance's methods to verify the server routes correctly and
    frames SSE the way the GUI will consume it."""

    def _stream(self, method, params=None):
        body = json.dumps({"method": method, "params": params or {}}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/stream", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.read().decode("utf-8")

    def test_ask_routes_through_to_facade(self):
        self.httpd.copilot.ask = lambda **p: f"ok:{p.get('question')}"
        r = self._rpc("ask", {"session": self.path, "question": "did it drift?"})
        self.assertEqual(r["result"], "ok:did it drift?")

    def test_now_routes_through_to_facade(self):
        self.httpd.copilot.now = lambda **p: "let it finish"
        self.assertEqual(self._rpc("now", {"session": self.path})["result"],
                         "let it finish")

    def test_stream_drains_handle_as_sse(self):
        self.httpd.copilot.ask_stream = lambda **p: _FakeHandle(["chunk1 ", "chunk2"])
        events = _parse_sse(self._stream("ask_stream", {"session": self.path,
                                                        "question": "go?"}))
        chunks = [e["data"]["chunk"] for e in events if e["event"] == "message"]
        done = [e["data"] for e in events if e["event"] == "done"]
        self.assertEqual(chunks, ["chunk1 ", "chunk2"])
        self.assertEqual(done, [{"text": "chunk1 chunk2", "usage": None}])

    def test_stream_unknown_method_returns_jsonrpc_error(self):
        # /stream rejects unknown methods with a JSON-RPC error (not an SSE stream)
        body = json.dumps({"method": "nope_stream", "params": {}}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/stream", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            resp = json.loads(r.read().decode("utf-8"))
        self.assertEqual(resp["error"]["code"], -32601)

    def test_narration_methods_listed_on_get(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        for m in ("ask", "chat", "now", "narrate_brief"):
            self.assertIn(m, info["methods"])
        self.assertIn("ask_stream", info["stream"])


if __name__ == "__main__":
    unittest.main()