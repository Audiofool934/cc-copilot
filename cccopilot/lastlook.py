"""Remember where a human last looked at a session, so cc-copilot can show
"what changed while you were away" when they come back.

A last-look marker is a tiny record per observed session — the transcript line
the human had seen up to, plus timestamps for display. It lives in the same
state home as Cockpit history (``$CC_COPILOT_STATE_DIR`` …), **never** under
``~/.claude`` or ``~/.codex``, and honors the same persistence opt-out.

The marker is deliberately small and best-effort: losing it just means the next
``/since last-look`` falls back to "no marker yet". Concurrent cockpits are
serialized with the store's lock so two windows can't corrupt the file.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from . import store as ST


def _path() -> str:
    return os.path.join(ST.state_home(), "lastlook.json")


def enabled() -> bool:
    """Last-look shares the history opt-out — it is local persisted state."""
    return ST.enabled()


def key_for(session_id: str = "", path: str = "") -> str:
    """A stable key for a session: its id, else the transcript basename."""
    if session_id:
        return session_id
    if path:
        base = os.path.basename(path)
        return base[:-6] if base.endswith(".jsonl") else base
    return ""


def _read() -> dict:
    """The whole marker map. Reads are lock-free and safe: writers swap the file
    atomically (os.replace), so a reader always sees a complete old or new file,
    never a half-truncated one."""
    try:
        with open(_path(), "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _sanitize(v) -> Optional[dict]:
    """Coerce a stored marker into a well-typed ``{line:int, ts, looked_at}``.

    A hand-edited or partially-written value (``{"line": "bad"}``) must never
    crash a caller's ``int(...)`` — fall back to line 0 (show everything)."""
    if not isinstance(v, dict):
        return None
    try:
        line = int(v.get("line", 0) or 0)
    except (TypeError, ValueError):
        line = 0
    return {"line": line, "ts": str(v.get("ts", "") or ""),
            "looked_at": str(v.get("looked_at", "") or "")}


def get(key: str) -> Optional[dict]:
    """The stored marker ``{line, ts, looked_at}`` for ``key``, or None."""
    if not key or not enabled():
        return None
    return _sanitize(_read().get(key))


def _update(mutate) -> None:
    """Serialize writers on a sidecar lock, apply ``mutate(data)``, then publish
    atomically with a temp file + os.replace (torn-read-proof). Best-effort."""
    if not enabled():
        return
    try:
        home = ST.state_home()
        os.makedirs(home, 0o700, exist_ok=True)
        p = _path()
        lock_path = p + ".lock"
        lf = open(lock_path, "w")
        try:
            with ST._locked(lf):
                data = _read()                       # current, under the writer lock
                if not isinstance(data, dict):
                    data = {}
                mutate(data)
                tmp = f"{p}.tmp-{os.getpid()}"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, p)                   # atomic publish
                ST._chmod(p, 0o600)
                ST._fsync_dir(home)
        finally:
            lf.close()
    except OSError:
        pass


def mark(key: str, line: int, ts: str = "", looked_at: str = "") -> None:
    """Record that the human has seen ``key`` up to transcript ``line``.

    Best-effort: any storage error is swallowed so it never breaks an answer or
    the read-only contract.
    """
    if not key or not enabled():
        return
    try:
        n = int(line)
    except (TypeError, ValueError):
        n = 0
    _update(lambda data: data.__setitem__(
        key, {"line": n, "ts": ts or "", "looked_at": looked_at or ""}))


def advance(key: str, line: int, ts: str = "", looked_at: str = "") -> None:
    """Move the marker forward to ``line`` only if it is newer than what's stored.

    A ``/since`` recap captures the tail when invoked but consumes the marker only
    once it renders (possibly seconds later, on a worker thread). Meanwhile another
    ``/since --raw`` or a second cockpit can advance the same key to a newer tail.
    Writing the older captured line would rewind the marker and re-surface already
    reviewed lines — so the compare-and-set happens here, atomically under the
    writer lock, and never goes backward.
    """
    if not key or not enabled():
        return
    try:
        n = int(line)
    except (TypeError, ValueError):
        return

    def _mut(data):
        cur = _sanitize(data.get(key)) or {"line": 0}
        if n > cur["line"]:
            data[key] = {"line": n, "ts": ts or "", "looked_at": looked_at or ""}
    _update(_mut)


def forget(key: str) -> None:
    """Drop the marker for ``key`` (best-effort)."""
    if not key or not enabled():
        return
    _update(lambda data: data.pop(key, None))
