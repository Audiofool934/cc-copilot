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
    try:
        with open(_path(), "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get(key: str) -> Optional[dict]:
    """The stored marker ``{line, ts, looked_at}`` for ``key``, or None."""
    if not key or not enabled():
        return None
    v = _read().get(key)
    return v if isinstance(v, dict) else None


def mark(key: str, line: int, ts: str = "", looked_at: str = "") -> None:
    """Record that the human has seen ``key`` up to transcript ``line``.

    Best-effort: any storage error is swallowed so it never breaks an answer or
    the read-only contract.
    """
    if not key or not enabled():
        return
    try:
        home = ST.state_home()
        os.makedirs(home, 0o700, exist_ok=True)
        p = _path()
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8", errors="replace") as fh:
            with ST._locked(fh):
                fh.seek(0)
                raw = fh.read()
                try:
                    data = json.loads(raw) if raw.strip() else {}
                    if not isinstance(data, dict):
                        data = {}
                except json.JSONDecodeError:
                    data = {}
                data[key] = {"line": int(line), "ts": ts or "", "looked_at": looked_at or ""}
                fh.seek(0)
                fh.truncate()
                json.dump(data, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
                ST._fsync_dir(home)
        ST._chmod(p, 0o600)
    except OSError:
        pass


def forget(key: str) -> None:
    """Drop the marker for ``key`` (best-effort)."""
    if not key or not enabled():
        return
    try:
        p = _path()
        if not os.path.exists(p):
            return
        fd = os.open(p, os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8", errors="replace") as fh:
            with ST._locked(fh):
                fh.seek(0)
                raw = fh.read()
                try:
                    data = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    data = {}
                if isinstance(data, dict) and key in data:
                    del data[key]
                    fh.seek(0)
                    fh.truncate()
                    json.dump(data, fh, ensure_ascii=False)
                    fh.flush()
                    os.fsync(fh.fileno())
    except OSError:
        pass
