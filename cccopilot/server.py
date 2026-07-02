"""Localhost JSON-RPC server over the Copilot facade - the wire layer a GUI
(Tauri + TypeScript) calls to drive cc-copilot's deterministic core.

Zero-dependency: stdlib ``http.server`` only, matching the core's
``dependencies = []``. Binds ``127.0.0.1`` exclusively - localhost is the trust
boundary, so there is no auth. The ``serve`` entrypoint binds an ephemeral port
by default and prints it, so the Tauri shell (and tests) can discover it.

Stage 2 (Option A): request/response only. Each public facade method becomes a
JSON-RPC method that returns its result. Live streaming / watch is a later
stage; the facade has no streaming methods yet, so neither does the server.

JSON-RPC 2.0 error codes:
  -32700 parse error · -32600 invalid request · -32601 method not found ·
  -32602 invalid params · -32000 server error · -32001 session not found
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from . import api as API
from . import serialize as SER


_ERR_PARSE = -32700
_ERR_INVALID_REQUEST = -32600
_ERR_METHOD_NOT_FOUND = -32601
_ERR_INVALID_PARAMS = -32602
_ERR_SERVER = -32000
_ERR_SESSION_NOT_FOUND = -32001


class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# method name -> serializer (None for methods that already return JSON-safe
# str/int/float/None/list-of-primitives). Object-returning methods get a
# serializer so the wire payload is typed JSON, not a Python repr.
_PUBLIC = {
    "sessions": SER.session_ref_to_dict,      # returns list[SessionRef]
    "projects": None,                          # returns list[tuple] (JSON arrays)
    "resolve": None,                           # returns str | None
    "current_session_path": None,             # returns str | None
    "transcript": SER.transcript_to_dict,     # returns Transcript
    "state": SER.state_to_dict,               # returns State
    "brief": None,                             # returns str
    "check": None,                             # returns str
    "check_verdict": None,                     # returns int
    "observe": None,                           # returns str
    "since": None,                             # returns str
    "advance_since_mark": None,                # returns dict | None
}


def _invoke(cp: API.Copilot, method: str, params: Any) -> Any:
    if method not in _PUBLIC:
        raise _RpcError(_ERR_METHOD_NOT_FOUND, f"method not found: {method}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise _RpcError(_ERR_INVALID_PARAMS, "params must be an object")
    fn = getattr(cp, method)
    try:
        value = fn(**params)
    except API.SessionNotFound as e:
        raise _RpcError(_ERR_SESSION_NOT_FOUND, str(e))
    except TypeError as e:
        # wrong/missing kwargs -> invalid params, not a server error
        raise _RpcError(_ERR_INVALID_PARAMS, str(e))
    except ValueError as e:
        # bad scope / bad `when` / unknown session selector -> invalid params
        raise _RpcError(_ERR_INVALID_PARAMS, str(e))
    ser = _PUBLIC[method]
    if ser is None:
        return value
    if value is None:
        return None
    if isinstance(value, list):
        return [ser(v) for v in value]
    return ser(value)


def _result(req_id: Any, result: Any) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}).encode("utf-8")


def _error(req_id: Any, code: int, message: str, data: Any = None) -> bytes:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "error": err}).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    server_version = "cc-copilot/0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass   # silence stderr access log; the server is a background process

    def do_GET(self):
        # a tiny discovery/health endpoint: the method list, for GUIs and `curl`.
        body = json.dumps({"jsonrpc": "2.0", "methods": sorted(_PUBLIC)}).encode("utf-8")
        self._send(200, body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            self._send(200, _error(None, _ERR_PARSE, "parse error"))
            return
        if not isinstance(payload, dict):
            self._send(200, _error(None, _ERR_INVALID_REQUEST,
                                   "invalid request: expected a JSON-RPC object"))
            return
        req_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params", {})
        if not isinstance(method, str):
            self._send(200, _error(req_id, _ERR_INVALID_REQUEST, "invalid method"))
            return
        try:
            result = _invoke(self.server.copilot, method, params)
        except _RpcError as e:
            self._send(200, _error(req_id, e.code, e.message, e.data))
            return
        except Exception as e:  # never let a facade bug crash the connection
            self._send(200, _error(req_id, _ERR_SERVER, f"server error: {e}"))
            return
        self._send(200, _result(req_id, result))

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


def make_server(port: int = 0, agents=None, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Build the server bound to ``host:port`` without serving. For tests and
    embedding; ``serve`` is the blocking entrypoint for the CLI."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.copilot = API.Copilot(agents=agents)
    return httpd


def serve(port: int = 0, agents=None, host: str = "127.0.0.1") -> int:
    """Bind and serve forever. Prints the bound address to stdout so a parent
    process (the Tauri shell) or a test can discover the actual port."""
    httpd = make_server(port=port, agents=agents, host=host)
    actual = httpd.server_address[1]
    sys.stdout.write(f"cc-copilot serve: listening on http://{host}:{actual}\n")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0