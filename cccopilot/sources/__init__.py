"""Agent-source dispatch.

The rest of cc-copilot calls these module-level functions instead of a specific
agent's ``locate``/``transcript`` module. They route by source so the cockpit,
status board, observer, and evidence engine work the same whether a session was
written by Claude Code or Codex.

Selecting which agents are in scope:
  ``CC_COPILOT_AGENTS`` (env, comma/space separated)  >
  ``[agents] enabled`` (config)                         >
  all registered sources that are available on this machine.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from ..locate import SessionRef
from ..transcript import Transcript
from .base import AgentSource, all_sources, register
from .claude import ClaudeSource
from .codex import CodexSource

# Order matters: most-specific first, Claude last as the catch-all default.
register(CodexSource())
register(ClaudeSource())


def _default() -> AgentSource:
    """The fallback source for unrecognized paths (Claude Code, the original)."""
    for s in all_sources():
        if isinstance(s, ClaudeSource):
            return s
    return all_sources()[-1]


def _configured_names() -> Optional[List[str]]:
    """The agent allow-list, or None to mean 'all available'."""
    env = os.environ.get("CC_COPILOT_AGENTS")
    if env is not None:
        names = [p.strip().lower() for p in env.replace(",", " ").split() if p.strip()]
        return names or None
    try:
        from .. import config
        names = config.agents_enabled()
    except Exception:
        names = None
    return names


def enabled_sources(agents: Optional[List[str]] = None) -> List[AgentSource]:
    """Registered sources that are available and in scope, specific-first.

    ``agents`` (an explicit filter, e.g. from ``--agent``) overrides config.
    """
    if agents:
        want = {a.strip().lower() for a in agents}
    else:
        cfg = _configured_names()
        want = {a.strip().lower() for a in cfg} if cfg else None
    out = []
    for s in all_sources():
        if want is not None and s.name not in want:
            continue
        try:
            if s.available():
                out.append(s)
        except Exception:
            continue
    return out


def source_for_path(path: str) -> AgentSource:
    """Which source owns ``path`` — used to route :func:`parse`/title/cwd."""
    for s in all_sources():
        try:
            if s.owns(path):
                return s
        except Exception:
            continue
    return _default()


def current_session_ids() -> List[str]:
    """Current live session ids exposed by any registered agent source."""
    out: List[str] = []
    seen = set()
    for s in all_sources():
        try:
            sid = s.current_session_id()
        except Exception:
            sid = ""
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def current_session_id() -> str:
    ids = current_session_ids()
    return ids[0] if ids else ""


def current_session_path() -> Optional[str]:
    """Best current live transcript path across agent sources.

    If several sources expose a current session, prefer the one most recently
    written, which is the session most likely to be live.
    """
    matches = []
    for s in all_sources():
        try:
            p = s.current_session_path()
        except Exception:
            p = None
        if not p:
            continue
        try:
            matches.append((os.path.getmtime(p), p))
        except OSError:
            continue
    return max(matches)[1] if matches else None


# ---- per-session dispatch ----------------------------------------------
def parse(path: str) -> Transcript:
    return source_for_path(path).parse(path)


def read_cwd(path: str) -> Optional[str]:
    return source_for_path(path).read_cwd(path)


def read_title(path: str, session_id: str = "") -> str:
    return source_for_path(path).read_title(path, session_id)


# ---- discovery dispatch -------------------------------------------------
def list_sessions(cwd: str, include_own: bool = False,
                  agents: Optional[List[str]] = None) -> List[SessionRef]:
    """Every in-scope agent's sessions for ``cwd``'s project, newest first."""
    refs: List[SessionRef] = []
    for s in enabled_sources(agents):
        try:
            refs.extend(s.list_sessions(cwd, include_own=include_own))
        except Exception:
            continue
    refs.sort(key=lambda r: r.mtime, reverse=True)
    return refs


def projects_with_sessions(limit: int = 8,
                           agents: Optional[List[str]] = None) -> List[Tuple[str, int, float]]:
    """Projects (across agents) that have real sessions, newest first.

    Merges per-cwd so a project worked by both agents counts once.
    """
    merged: dict = {}
    for s in enabled_sources(agents):
        try:
            rows = s.projects_with_sessions(limit)
        except Exception:
            continue
        for cwd, count, mtime in rows:
            key = os.path.abspath(cwd) if cwd else cwd
            if key in merged:
                c0, m0 = merged[key][1], merged[key][2]
                merged[key] = (cwd, c0 + count, max(m0, mtime))
            else:
                merged[key] = (cwd, count, mtime)
    out = sorted(merged.values(), key=lambda x: -x[2])
    return out[:limit]


def resolve(cwd: str, session: Optional[str] = None, include_current: bool = False,
            agents: Optional[List[str]] = None) -> Optional[str]:
    """Resolve a transcript path across in-scope agents.

    - ``session`` may be a full path, a session id, or a prefix (matched against
      any in-scope agent).
    - Otherwise the most-recently-modified session for ``cwd`` across agents,
      excluding the *current* Claude session by default so ``brief`` from inside
      a live session reports on the agent you want to watch.
    """
    if session:
        if os.path.isfile(session):
            return session
        for ref in list_sessions(cwd, agents=agents):
            if ref.session_id == session or ref.session_id.startswith(session):
                return ref.path
        return None

    sessions = list_sessions(cwd, agents=agents)
    if not sessions:
        return None
    self_ids = set() if include_current else set(current_session_ids())
    for ref in sessions:
        if any(ref.session_id == sid or ref.session_id.startswith(sid)
               or sid.startswith(ref.session_id) for sid in self_ids):
            continue
        return ref.path
    return sessions[0].path
