"""The Copilot core facade - a programmatic API over the deterministic core.

cc-copilot has two presenters today (the CLI and the Textual cockpit), and both
re-implement the same glue: resolve a session, parse its transcript, build the
State, then call a renderer. This module centralizes that glue behind one
object so a third presenter - the GUI - can be just another consumer of the
core instead of a fork of it.

It is read-only with respect to observed agents: it never writes to a
transcript. The only state it mutates is cc-copilot's own last-look marker, and
only via the explicit ``advance_since_mark`` opt-in - the default ``since``
call is a peek that reads without advancing. Narration, chat, and watch
streaming live in ``narrate`` and the ``Backend`` layer; they are wrapped in a
later stage.

Everything here delegates to existing functions - it adds no new logic. The
reading surfaces return the same Markdown strings the CLI prints, so parity is
structural and pinnable by tests.
"""

from __future__ import annotations

import datetime
import os
from typing import List, Optional, Tuple

from . import brief as B
from . import lastlook as LL
from . import observe as O
from . import scope as SC
from . import since as SI
from . import sources as SRC
from . import state as S
from .locate import SessionRef
from .state import State
from .transcript import Transcript


class SessionNotFound(LookupError):
    """Raised when no session can be resolved for a given cwd/session argument."""


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


class Copilot:
    """Programmatic facade over cc-copilot's deterministic, read-only core.

    Hold one instance per scope of interest. ``agents`` (an optional agent
    filter list, mirroring ``--agent`` / ``CC_COPILOT_AGENTS``) is forwarded to
    the agent-source dispatch and applied to every discovery call.

    The reading surfaces (``brief``, ``check``, ``observe``, ``since``) return
    the same Markdown strings the CLI prints; the low-level accessors
    (``transcript``, ``state``) hand back the presentation-free data model so a
    GUI can render timelines and diffs natively.
    """

    def __init__(self, agents: Optional[List[str]] = None):
        self._agents = agents

    # ---- session discovery -------------------------------------------------

    def sessions(self, cwd: str, include_current: bool = False) -> List[SessionRef]:
        """In-scope agent sessions for ``cwd``'s project, newest first."""
        return SRC.list_sessions(cwd, include_own=include_current, agents=self._agents)

    def projects(self, limit: int = 8) -> List[Tuple[str, int, float]]:
        """Projects (across agents) that have real session, newest first."""
        return SRC.projects_with_sessions(limit, agents=self._agents)

    def resolve(self, cwd: str, session: Optional[str] = None,
                include_current: bool = False) -> Optional[str]:
        """Resolve a transcript path for ``cwd`` / ``session`` across in-scope agents.

        ``session`` may be a full path, a session id, or a prefix. With no
        ``session``, the most-recently-modified session for ``cwd`` is returned
        (excluding the current live session unless ``include_current``).
        Returns ``None`` if nothing matches.
        """
        return SRC.resolve(cwd, session, include_current=include_current,
                            agents=self._agents)

    def current_session_path(self) -> Optional[str]:
        """Best current live transcript path across in-scope agents, or None."""
        return SRC.current_session_path()

    # ---- low-level access (presentation-free data model) ------------------

    def transcript(self, path: str) -> Transcript:
        """Parse a transcript file into the ``Transcript`` data model."""
        return SRC.parse(path)

    def state(self, path: str) -> State:
        """Build the ``State`` (assessed view) of a transcript path."""
        return S.build(self.transcript(path))

    # ---- reading surfaces (Markdown strings) -----------------------------

    def brief(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
              scope: str = SC.SESSION, scope_sessions: str = "",
              include_current: bool = False,
              max_files: int = 12, max_cmds: int = 6) -> str:
        """The deterministic, evidence-cited brief (what ``cc-copilot brief`` prints).

        For ``scope = session`` (the default) this is ``brief.render``; wider
        scopes delegate to ``scope.render_evidence`` exactly as the CLI does.
        """
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        if sc == SC.SESSION:
            return B.render(st, max_files=max_files, max_cmds=max_cmds)
        return SC.render_evidence(path, st, sc, sessions=scope_sessions).text

    def check(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
              scope: str = SC.SESSION, scope_sessions: str = "",
              include_current: bool = False) -> str:
        """The focused 'can I leave it running?' report (what ``cc-copilot check`` prints)."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        if sc == SC.SESSION:
            return B.render_check(st)
        return SC.render_evidence(path, st, sc, sessions=scope_sessions).text

    def check_verdict(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                      scope: str = SC.SESSION, scope_sessions: str = "",
                      include_current: bool = False) -> int:
        """Scriptable verdict: 2 intervene, 1 review, 0 clear-ish."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        return SC.exit_code(path, st, SC.normalize(scope), sessions=scope_sessions)

    def observe(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                scope: str = SC.SESSION, scope_sessions: str = "",
                include_current: bool = False) -> str:
        """The 'where should my attention go right now?' board (what ``cc-copilot observe`` prints)."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        return O.render(path, st, SC.normalize(scope), sessions=scope_sessions)

    def since(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
              when: str = "last-look", peek: bool = True,
              include_current: bool = False) -> str:
        """What changed since ``when`` (what ``cc-copilot since`` prints, deterministic path).

        ``when`` is ``"last-look"`` (the stored marker for this session) or a
        duration like ``"30m"`` / ``"2h"`` / ``"1d"``.

        With ``peek=True`` (the default) the call is read-only: it never creates
        or advances the last-look marker, so a GUI can re-render freely. With
        ``peek=False`` it mirrors ``cc-copilot since`` - recording a marker on
        first use and advancing it forward-only once the delta is rendered.
        """
        path = self._require(cwd, session, include_current)
        tr = SRC.parse(path)
        st = S.build(tr)
        key = LL.key_for(getattr(tr, "session_id", "") or "", path)
        cur_line = tr.records[-1].line if tr.records else 0
        cur_ts = tr.records[-1].raw_ts if tr.records else ""
        when = (when or "last-look").strip().lower()

        if when in ("last-look", "lastlook", "last"):
            if not LL.enabled():
                return ("last-look tracking is off (persistence disabled via "
                        "CC_COPILOT_HISTORY=0 or [history] enabled=false).\n"
                        "Use a time window instead, e.g. "
                        "Copilot().since(cwd, when='30m').")
            mark = LL.get(key)
            if mark is None:
                if peek:
                    return (f"No last-look mark for `{key[:8]}` yet. Call "
                            f"advance_since_mark() to record your current position "
                            f"(L{cur_line}), then re-run since() after the agent "
                            f"works. (Or use a time window: when='30m'.)")
                LL.mark(key, cur_line, cur_ts, _now_iso())
                return (f"No last-look mark for `{key[:8]}` yet - recorded your "
                        f"current position (L{cur_line}).\nRe-run since() after the "
                        f"agent works to see what changed. (Or when='30m' for a "
                        f"time window.)")
            view = SI.build(tr, st, since_line=int(mark.get("line", 0) or 0),
                            label="last look", looked_at=mark.get("looked_at", ""))
            if not peek:
                LL.advance(key, cur_line, cur_ts, _now_iso())
            return view.text

        secs = SI.parse_duration(when)
        if secs is None:
            raise ValueError(f"unknown time {when!r}; use 'last-look' or a "
                             f"duration like 30m / 2h / 1d")
        view = SI.build(tr, st, seconds=secs, label=when)
        return view.text

    def advance_since_mark(self, cwd: Optional[str] = None,
                           session: Optional[str] = None) -> Optional[dict]:
        """Record that the human has seen this session up to its current tail.

        Creates the last-look marker if none exists, else advances it
        forward-only. Returns the marker ``{line, ts, looked_at}`` after the
        update, or ``None`` if last-look tracking is disabled or the session has
        no records. This is the only mutating method on the facade.
        """
        path = self._require(cwd, session, include_current=False)
        tr = SRC.parse(path)
        if not tr.records or not LL.enabled():
            return None
        key = LL.key_for(getattr(tr, "session_id", "") or "", path)
        cur_line = tr.records[-1].line
        cur_ts = tr.records[-1].raw_ts
        LL.advance(key, cur_line, cur_ts, _now_iso())
        return LL.get(key)

    # ---- internal ---------------------------------------------------------

    def _require(self, cwd: Optional[str], session: Optional[str],
                 include_current: bool) -> str:
        # A session that is already a file path resolves directly - cwd is
        # irrelevant in that case (mirrors sources.resolve's isfile shortcut),
        # so callers who already hold a transcript path need not pass a cwd.
        if session and os.path.isfile(session):
            return session
        if not cwd:
            label = f"session {session!r}" if session else "the current project"
            raise SessionNotFound(
                f"no cc-copilot session: pass a cwd or a session path ({label})")
        path = self.resolve(cwd, session, include_current=include_current)
        if not path:
            label = f"session {session!r}" if session else f"cwd {cwd!r}"
            raise SessionNotFound(f"no cc-copilot session found for {label}")
        return path