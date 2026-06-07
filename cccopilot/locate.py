"""Find Claude Code session transcripts on disk.

Claude Code stores transcripts under
``${CLAUDE_CONFIG_DIR:-~/.claude}/projects/<encoded>/`` where ``<encoded>`` is
the project's absolute cwd with every non-alphanumeric character replaced by
``-`` (verified: ``/Users/audiofool/Projects`` ->
``-Users-audiofool-Projects``; ``audiofool.github.io`` -> ``audiofool-github-io``).

We use the encoding as a fast path, but always fall back to scanning, since the
authoritative cwd is recorded *inside* each transcript.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional


def claude_home() -> str:
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")


def projects_root() -> str:
    return os.path.join(claude_home(), "projects")


def sessions_root() -> str:
    return os.path.join(claude_home(), "sessions")


def encode_cwd(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(cwd))


def project_dir_for(cwd: str) -> Optional[str]:
    d = os.path.join(projects_root(), encode_cwd(cwd))
    return d if os.path.isdir(d) else None


@dataclass
class SessionRef:
    path: str
    session_id: str
    mtime: float
    size: int
    title: str = ""
    own: bool = False        # a cc-copilot narration call, not a real work session

    @property
    def hhmm(self) -> str:
        from datetime import datetime
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M")


# Unique signature of cc-copilot's own narration prompt (see narrate._PREAMBLE).
# Using `claude -p`/`codex exec` as a backend logs a session transcript per call;
# we recognize and hide our own so they never masquerade as the user's sessions.
_OWN_SIG = b"cc-copilot's narration layer"


def is_own_session(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(16384)   # the prompt is the first user message, near the top
    except OSError:
        return False
    return _OWN_SIG in head


def _session_meta_name(session_id: str) -> str:
    if not session_id:
        return ""
    d = sessions_root()
    try:
        names = os.listdir(d)
    except OSError:
        return ""
    best_name, best_updated = "", -1
    for name in names:
        if not name.endswith(".json"):
            continue
        p = os.path.join(d, name)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                o = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(o, dict) or o.get("sessionId") != session_id:
            continue
        n = o.get("name")
        if not isinstance(n, str) or not n.strip():
            continue
        updated = int(o.get("updatedAt", 0) or 0)
        if updated >= best_updated:
            best_name, best_updated = n.strip(), updated
    return best_name


def read_title(path: str, session_id: str = "") -> str:
    """Latest Claude Code title recorded inside a transcript, if present.

    Claude has used both ``type: ai-title``/``aiTitle`` and
    ``type: custom-title``/``customTitle`` for session names. Renames append a
    later title event, so latest wins. Active sessions can also expose the name
    under ``${CLAUDE_CONFIG_DIR:-~/.claude}/sessions/*.json``; use that as a
    fallback.
    """
    title = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(o, dict):
                    continue
                if o.get("sessionId") and not session_id:
                    session_id = str(o.get("sessionId"))
                if o.get("type") in ("ai-title", "custom-title"):
                    t = o.get("aiTitle") or o.get("customTitle")
                    if isinstance(t, str) and t.strip():
                        title = t.strip()
    except OSError:
        pass
    return title or _session_meta_name(session_id)


def _refs_in(d: str, include_own: bool = False) -> list:
    refs = []
    for name in os.listdir(d):
        if not name.endswith(".jsonl"):
            continue
        p = os.path.join(d, name)
        try:
            stt = os.stat(p)
        except OSError:
            continue
        refs.append(SessionRef(
            path=p, session_id=name[:-6], mtime=stt.st_mtime, size=stt.st_size,
            title=read_title(p, name[:-6]), own=is_own_session(p),
        ))
    refs.sort(key=lambda r: r.mtime, reverse=True)
    return refs if include_own else [r for r in refs if not r.own]


def refs_in_dir(d: str, include_own: bool = False) -> list:
    """Return session refs from an already-known project transcript directory."""
    return _refs_in(d, include_own) if os.path.isdir(d) else []


def list_sessions(cwd: str, include_own: bool = False) -> list:
    """Return session transcripts for ``cwd``'s project, newest first.

    cc-copilot's own narration sessions are hidden by default (``include_own``).
    """
    d = project_dir_for(cwd)
    return _refs_in(d, include_own) if d else []


def read_cwd(path: str) -> Optional[str]:
    """The authoritative project cwd recorded inside a transcript."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(40):
                line = f.readline()
                if not line:
                    break
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(o, dict) and o.get("cwd"):
                    return o["cwd"]
    except OSError:
        pass
    return None


def projects_with_sessions(limit: int = 8) -> list:
    """Across all projects, the ones with real (non-own) sessions, newest first.
    Returns [(cwd, count, mtime), …] — used to turn a 'no session here' error
    into a launchpad."""
    root = projects_root()
    try:
        buckets = os.listdir(root)
    except OSError:
        return []
    out = []
    for b in buckets:
        d = os.path.join(root, b)
        if not os.path.isdir(d):
            continue
        try:
            refs = _refs_in(d)
        except OSError:
            continue
        if not refs:
            continue
        cwd = read_cwd(refs[0].path) or ""
        out.append((cwd, len(refs), refs[0].mtime))
    out.sort(key=lambda x: -x[2])
    return out[:limit]


def ago(mtime: float) -> str:
    s = max(0, time.time() - mtime)
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < 86400:
        return f"{int(s // 3600)}h"
    return f"{int(s // 86400)}d"


def resolve(cwd: str, session: Optional[str] = None,
            include_current: bool = False) -> Optional[str]:
    """Resolve a transcript path.

    - ``session`` may be a full path, a session id, or a prefix.
    - Otherwise return the most recently modified session for ``cwd``. By
      default this excludes the *current* session if we can detect it, so
      ``brief`` from inside a live session reports on the agent you actually
      want to watch. ``include_current=True`` is the explicit "latest" path.
    """
    if session:
        if os.path.isfile(session):
            return session
        for ref in list_sessions(cwd):
            if ref.session_id == session or ref.session_id.startswith(session):
                return ref.path
        return None

    sessions = list_sessions(cwd)
    if not sessions:
        return None
    self_id = "" if include_current else os.environ.get("CLAUDE_SESSION_ID", "")
    for ref in sessions:
        if self_id and ref.session_id == self_id:
            continue
        return ref.path
    return sessions[0].path
