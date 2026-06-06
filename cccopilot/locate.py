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

    @property
    def hhmm(self) -> str:
        from datetime import datetime
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M")


def list_sessions(cwd: str) -> list:
    """Return session transcripts for ``cwd``'s project, newest first."""
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
        ))
    refs.sort(key=lambda r: r.mtime, reverse=True)
    return refs


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
