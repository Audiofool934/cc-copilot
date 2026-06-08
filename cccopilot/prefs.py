"""Small persisted cockpit UI preferences (e.g. panel heights).

Stored as ``ui.json`` in the same state home as history (never under
``~/.claude`` / ``~/.codex``). Best-effort and non-sensitive: a write failure or
a corrupt file just falls back to defaults. An env override (``CC_COPILOT_<KEY>``)
wins so a value can be pinned without a config file.
"""

from __future__ import annotations

import json
import os

from . import store as ST


def _path() -> str:
    return os.path.join(ST.state_home(), "ui.json")


def _read() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8", errors="replace") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get_int(key: str, default: int) -> int:
    """Persisted int for ``key`` — env ``CC_COPILOT_<KEY>`` wins, then ui.json."""
    env = os.environ.get("CC_COPILOT_" + key.upper())
    if env is not None:
        try:
            return int(env)
        except ValueError:
            pass
    try:
        return int(_read().get(key, default))
    except (TypeError, ValueError):
        return default


def set(key: str, value) -> None:
    """Persist ``key`` atomically (temp + os.replace). Best-effort."""
    try:
        home = ST.state_home()
        os.makedirs(home, 0o700, exist_ok=True)
        data = _read()
        data[key] = value
        p = _path()
        tmp = f"{p}.tmp-{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        ST._chmod(p, 0o600)
    except OSError:
        pass
