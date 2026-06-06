"""Find Claude Code session transcripts on disk.

Claude Code stores transcripts under ``~/.claude/projects/<encoded>/`` where
``<encoded>`` is the project's absolute cwd with every non-alphanumeric
character replaced by ``-`` (verified: ``/Users/audiofool/Projects`` ->
``-Users-audiofool-Projects``; ``audiofool.github.io`` -> ``audiofool-github-io``).

We use the encoding as a fast path, but always fall back to scanning, since the
authoritative cwd is recorded *inside* each transcript.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional


def projects_root() -> str:
    return os.path.expanduser("~/.claude/projects")


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


def list_sessions(cwd: str, include_own: bool = False) -> list:
    """Return session transcripts for ``cwd``'s project, newest first.

    cc-copilot's own narration sessions are hidden by default (``include_own``).
    """
    d = project_dir_for(cwd)
    if d is None:
        return []
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
            own=is_own_session(p),
        ))
    refs.sort(key=lambda r: r.mtime, reverse=True)
    return refs if include_own else [r for r in refs if not r.own]


def resolve(cwd: str, session: Optional[str] = None) -> Optional[str]:
    """Resolve a transcript path.

    - ``session`` may be a full path, a session id, or a prefix.
    - Otherwise return the most recently modified session for ``cwd`` —
      excluding the *current* session if we can detect it, so ``brief`` from
      inside a live session reports on the agent you actually want to watch.
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
    self_id = os.environ.get("CLAUDE_SESSION_ID", "")
    for ref in sessions:
        if self_id and ref.session_id == self_id:
            continue
        return ref.path
    return sessions[0].path
