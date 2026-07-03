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
from dataclasses import asdict
from typing import List, Optional, Tuple

from . import brief as B
from . import backends as BK
from . import chat as C
from . import context as EC
from . import handoff as HO
from . import lastlook as LL
from . import models as MODELS
from . import narrate as N
from . import observe as O
from . import onboard as OB
from . import scope as SC
from . import since as SI
from . import sources as SRC
from . import state as S
from . import store as ST
from .locate import SessionRef
from .narrate import StreamHandle
from .serialize import since_view_to_dict
from .state import State
from .transcript import Transcript


class SessionNotFound(LookupError):
    """Raised when no session can be resolved for a given cwd/session argument."""


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _drain(handle: StreamHandle) -> str:
    """Consume a StreamHandle to completion and return its joined text.

    Used by the non-streaming narration wrappers that have no blocking sibling
    in narrate (e.g. narrate_brief, which only exposes a stream variant by
    design - see test_narrate.test_dead_narrate_helpers_removed)."""
    for _ in handle:
        pass
    return handle.text


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
        """In-scope agent sessions for ``cwd``'s project, newest first.

        cc-copilot's own narration/helper transcripts are always excluded -
        they are not the coding-agent sessions a GUI observes. ``include_current``
        controls whether the live current session appears in the list (default
        False, matching the CLI's default resolution: a "what can I switch to"
        list excludes the session you're already in).
        """
        refs = SRC.list_sessions(cwd, include_own=False, agents=self._agents)
        if include_current:
            return refs
        self_ids = set(SRC.current_session_ids())
        if not self_ids:
            return refs
        return [r for r in refs
                if not any(r.session_id == sid or r.session_id.startswith(sid)
                           or sid.startswith(r.session_id) for sid in self_ids)]

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
        """Best current live transcript path across in-scope agents, or None.

        Honors the ``agents`` filter: when set, only sources matching it are
        considered, so a filtered facade never surfaces a live session from an
        excluded agent. With no filter this is exactly ``sources.current_session_path()``.
        """
        if not self._agents:
            return SRC.current_session_path()
        matches = []
        for s in SRC.enabled_sources(self._agents):
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
        return self._brief_text(path, st, SC.normalize(scope), scope_sessions,
                                max_files, max_cmds)

    def _brief_text(self, path: str, st, sc: str, scope_sessions: str,
                    max_files: int = 12, max_cmds: int = 6) -> str:
        """The brief Markdown for a pre-built state. Shared by the reading
        surface and the narration wrappers so they ground in the same evidence."""
        if sc == SC.SESSION:
            return B.render(st, max_files=max_files, max_cmds=max_cmds)
        return SC.render_evidence(path, st, sc, sessions=scope_sessions,
                                   agents=self._agents).text

    def check(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
              scope: str = SC.SESSION, scope_sessions: str = "",
              include_current: bool = False) -> str:
        """The focused 'can I leave it running?' report (what ``cc-copilot check`` prints)."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        if sc == SC.SESSION:
            return B.render_check(st)
        return SC.render_evidence(path, st, sc, sessions=scope_sessions,
                                   agents=self._agents).text

    def check_verdict(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                      scope: str = SC.SESSION, scope_sessions: str = "",
                      include_current: bool = False) -> int:
        """Scriptable verdict: 2 intervene, 1 review, 0 clear-ish."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        return SC.exit_code(path, st, SC.normalize(scope), sessions=scope_sessions,
                            agents=self._agents)

    def observe(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                scope: str = SC.SESSION, scope_sessions: str = "",
                include_current: bool = False) -> str:
        """The 'where should my attention go right now?' board (what ``cc-copilot observe`` prints)."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        return O.render(path, st, SC.normalize(scope), sessions=scope_sessions,
                         agents=self._agents)

    def _since_view(self, cwd, session, when, peek, include_current):
        """Build the since SinceView for ``when``, or return a status string
        (no last-look mark yet / tracking off). Shared by :meth:`since` (which
        returns the rendered text) and :meth:`diff` (which returns the
        structured dict). Raises ``ValueError`` for an unparseable duration."""
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
            return view

        secs = SI.parse_duration(when)
        if secs is None:
            raise ValueError(f"unknown time {when!r}; use 'last-look' or a "
                             f"duration like 30m / 2h / 1d")
        return SI.build(tr, st, seconds=secs, label=when)

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
        v = self._since_view(cwd, session, when, peek, include_current)
        return v.text if isinstance(v, SI.SinceView) else v

    def diff(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
             when: str = "30m", peek: bool = True,
             include_current: bool = False) -> dict:
        """The structured ``/diff`` view: the typed delta behind the ``since``
        text - new turns, messages, commands, failures, changed files, the
        status/verdict transition, and the pending-ask cue. ``when`` is a
        duration (``"30m"`` / ``"2h"`` / ``"1d"``) or ``"last-look"``.

        Returns ``{"message": ..., "nothing_new": true, "new_events": 0}`` when
        there is no last-look mark yet or tracking is off (no diff to render).
        """
        v = self._since_view(cwd, session, when, peek, include_current)
        if isinstance(v, SI.SinceView):
            return since_view_to_dict(v)
        return {"message": v, "nothing_new": True, "new_events": 0}

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

    # ---- narration (LLM) surfaces ------------------------------------------
    #
    # Wrappers over narrate.* + context.build that mirror the CLI's cmd_ask /
    # cmd_chat / cmd_now / `brief --narrate`. Each has a streaming sibling
    # returning a narrate.StreamHandle (for the server's SSE endpoint) and a
    # blocking sibling returning the full string. Read-only with respect to
    # the observed agent; the only LLM calls go to the configured backend,
    # grounded in cited evidence. ``model``/``backend`` are passed straight to
    # narrate (None = the default backend).

    def _ctx(self, path: str, st, sc: str, scope_sessions: str,
             question: str, history) -> str:
        """A question-aware evidence context (what cmd_ask/chat ground in)."""
        ctx = EC.build(path, st, sc, sessions=scope_sessions,
                       question=question, history=list(history or []),
                       project_context=True)
        return ctx.text

    def narrate_brief(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                      scope: str = SC.SESSION, scope_sessions: str = "",
                      include_current: bool = False,
                      model: str = None, backend=None) -> str:
        """LLM narration of the deterministic brief (``brief --narrate``).

        Drains the streaming sibling (narrate only exposes a stream variant by
        design) and returns the joined text."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        text = self._brief_text(path, st, SC.normalize(scope), scope_sessions)
        return _drain(N.narrate_brief_stream(text, model=model, backend=backend))

    def narrate_brief_stream(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                             scope: str = SC.SESSION, scope_sessions: str = "",
                             include_current: bool = False,
                             model: str = None, backend=None) -> StreamHandle:
        """Streaming sibling of :meth:`narrate_brief`."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        text = self._brief_text(path, st, SC.normalize(scope), scope_sessions)
        return N.narrate_brief_stream(text, model=model, backend=backend)

    def ask(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
            question: str, scope: str = SC.SESSION, scope_sessions: str = "",
            include_current: bool = False,
            model: str = None, backend=None) -> str:
        """Answer a question grounded in the session + project context (``ask``)."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        ctx = self._ctx(path, st, sc, scope_sessions, question, [])
        return N.ask_brief(ctx, question, model=model, backend=backend)

    def ask_stream(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                   question: str, scope: str = SC.SESSION, scope_sessions: str = "",
                   include_current: bool = False,
                   model: str = None, backend=None) -> StreamHandle:
        """Streaming sibling of :meth:`ask`."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        ctx = self._ctx(path, st, sc, scope_sessions, question, [])
        return N.ask_brief_stream(ctx, question, model=model, backend=backend)

    def chat(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
             history: list, question: str, scope: str = SC.SESSION,
             scope_sessions: str = "", include_current: bool = False,
             model: str = None, backend=None) -> str:
        """One grounded chat turn with prior history (``chat``).

        The GUI holds the conversation history and calls this per turn; the
        current evidence context is the only source of new observed facts.
        """
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        ctx = self._ctx(path, st, sc, scope_sessions, question, history)
        return N.chat_brief(ctx, history, question, model=model, backend=backend)

    def chat_stream(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                    history: list, question: str, scope: str = SC.SESSION,
                    scope_sessions: str = "", include_current: bool = False,
                    model: str = None, backend=None) -> StreamHandle:
        """Streaming sibling of :meth:`chat`."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        ctx = self._ctx(path, st, sc, scope_sessions, question, history)
        return N.chat_brief_stream(ctx, history, question, model=model, backend=backend)

    def now(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
            instruction: str = "", scope: str = SC.SESSION, scope_sessions: str = "",
            include_current: bool = False,
            model: str = None, backend=None, raw: bool = False) -> str:
        """Recommend the next step (``now``).

        With ``raw=True`` or no backend available, returns the deterministic
        observer recommendation (``observe.next_step``). Otherwise returns the
        LLM recommendation grounded in the brief, falling back to the
        deterministic one if the backend fails.
        """
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        det = O.next_step(path, st, sc, sessions=scope_sessions)
        if raw or not N.available(backend):
            return det
        text = self._brief_text(path, st, sc, scope_sessions)
        try:
            return N.next_step_brief(text, model=model, backend=backend,
                                      instruction=instruction)
        except Exception:
            return det

    def now_stream(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                   instruction: str = "", scope: str = SC.SESSION,
                   scope_sessions: str = "", include_current: bool = False,
                   model: str = None, backend=None) -> StreamHandle:
        """Streaming sibling of :meth:`now` (LLM path; the caller can fall back
        to the deterministic :meth:`now` if this raises)."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        text = self._brief_text(path, st, sc, scope_sessions)
        return N.next_step_brief_stream(text, model=model, backend=backend,
                                         instruction=instruction)

    def goal(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
             instruction: str = "", scope: str = SC.SESSION, scope_sessions: str = "",
             include_current: bool = False,
             model: str = None, backend=None, raw: bool = False) -> str:
        """Draft a paste-ready agent ``/goal`` command (``goal``).

        With ``raw=True`` or no backend, returns the deterministic draft
        (``chat._deterministic_goal``). Otherwise returns the LLM draft with a
        deterministic fallback appended, mirroring ``ChatSession._goal``.
        """
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        det = C._deterministic_goal(st, instruction)
        if raw or not N.available(backend):
            return det
        sc = SC.normalize(scope)
        ctx = self._ctx(path, st, sc, scope_sessions,
                        C._goal_context_question(instruction), [])
        try:
            rec = N.goal_brief(ctx, model=model, backend=backend, instruction=instruction)
        except Exception as e:
            return det + f"\n\n> _goal draft unavailable ({e}); deterministic draft above._"
        return C.ChatSession._compose_goal(rec, det)

    def goal_stream(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                    instruction: str = "", scope: str = SC.SESSION,
                    scope_sessions: str = "", include_current: bool = False,
                    model: str = None, backend=None) -> StreamHandle:
        """Streaming sibling of :meth:`goal` (LLM draft only; use :meth:`goal`
        with ``raw=True`` for the deterministic draft)."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        ctx = self._ctx(path, st, sc, scope_sessions,
                        C._goal_context_question(instruction), [])
        return N.goal_brief_stream(ctx, model=model, backend=backend,
                                    instruction=instruction)

    def loop(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
             instruction: str = "", scope: str = SC.SESSION, scope_sessions: str = "",
             include_current: bool = False,
             model: str = None, backend=None, raw: bool = False) -> str:
        """Draft a paste-ready agent ``/loop`` command (``loop``)."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        det = C._deterministic_loop(st, instruction)
        if raw or not N.available(backend):
            return det
        sc = SC.normalize(scope)
        ctx = self._ctx(path, st, sc, scope_sessions,
                        C._loop_context_question(instruction), [])
        try:
            rec = N.loop_brief(ctx, model=model, backend=backend, instruction=instruction)
        except Exception as e:
            return det + f"\n\n> _loop draft unavailable ({e}); deterministic draft above._"
        return C.ChatSession._compose_loop(rec, det)

    def loop_stream(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                    instruction: str = "", scope: str = SC.SESSION,
                    scope_sessions: str = "", include_current: bool = False,
                    model: str = None, backend=None) -> StreamHandle:
        """Streaming sibling of :meth:`loop` (LLM draft only)."""
        path = self._require(cwd, session, include_current)
        st = S.build(SRC.parse(path))
        sc = SC.normalize(scope)
        ctx = self._ctx(path, st, sc, scope_sessions,
                        C._loop_context_question(instruction), [])
        return N.loop_brief_stream(ctx, model=model, backend=backend,
                                     instruction=instruction)

    def recap_since(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                    when: str = "last-look", instruction: str = "",
                    include_current: bool = False,
                    model: str = None, backend=None) -> str:
        """Narrate the ``/since`` delta into a grounded re-entry recap
        (``since --recap``). Advances the last-look marker (like the CLI).

        If there is no last-look mark yet (or tracking is off), returns the
        ``since`` status message without narrating.
        """
        since_text = self.since(cwd, session, when=when, peek=False,
                                include_current=include_current)
        if (since_text.startswith("No last-look mark")
                or since_text.startswith("last-look tracking is off")):
            return since_text
        return N.recap_since(since_text, model=model, backend=backend,
                              instruction=instruction)

    def handoff(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                include_current: bool = False) -> str:
        """A shareable Markdown handoff brief for this session (``/handoff``):
        identity meta, a 'while you were away' since delta (if any), and the
        full brief, demoted under headings so it pastes cleanly."""
        path = self._require(cwd, session, include_current)
        tr = SRC.parse(path)
        st = S.build(tr)
        agent = SRC.source_for_path(path).name
        generated_at = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
        sv = self._since_view(cwd, session, "last-look", True, include_current)
        since_view = sv if isinstance(sv, SI.SinceView) else None
        return HO.render(st, agent=agent, generated_at=generated_at,
                         since_view=since_view)

    # ---- settings: backends / models --------------------------------------
    #
    # The model picker: list backends with availability + the active one, list
    # curated models, and write a chosen backend/model (+ optional API key) to
    # the cc-copilot config. set_backend mutates ~/.cc-copilot.toml (cc-copilot's
    # own config, not the observed agent); the other two are read-only.

    def backends(self) -> List[dict]:
        """LLM backends with availability and the active one marked."""
        try:
            reg = BK.registry()
            active = ""
            try:
                active = BK.resolve(None).name
            except Exception:
                pass
            out = []
            for name, be in sorted(reg.items()):
                try:
                    ch = OB.choice_for_or_none(name)
                    out.append({
                        "name": name,
                        "available": bool(be.available()),
                        "reason": "" if be.available() else be.reason(),
                        "active": name == active,
                        "needs_key": bool(getattr(be, "needs_key", False)),
                        "key_env": getattr(ch, "key_env", "") or "",
                        "default_model": getattr(be, "default_model", "") or "",
                    })
                except Exception:
                    continue
            return out
        except Exception:
            return []

    def models_for(self, name: str) -> List[dict]:
        """Curated models for a backend, as ``[{"id", "note"}, ...]``."""
        try:
            return [{"id": m.id, "note": m.note} for m in MODELS.models_for(name)]
        except Exception:
            return []

    def set_backend(self, name: str, *, model: str = "", key: str = "") -> str:
        """Write the chosen backend (and optional model + API key) to the cc-copilot
        config, preserving existing settings. Returns the config path written."""
        return OB.write_choice(name, model=model, key_value=key)

    def needs_onboarding(self) -> bool:
        """True on first run only: no config file yet (and not opted out)."""
        try:
            return bool(OB.needs_onboarding())
        except Exception:
            return False

    def onboard_choices(self, featured_only: bool = True) -> List[dict]:
        """Curated backend choices with readiness + status, for the welcome screen."""
        try:
            out = []
            for d in OB.detect(featured_only=featured_only):
                c = d.choice
                out.append({
                    "name": c.name, "label": c.label, "kind": c.kind, "blurb": c.blurb,
                    "key_env": c.key_env,
                    "default_model": getattr(c, "default_model", "") or "",
                    "featured": c.featured,
                    "brand_hex": getattr(c, "brand_hex", "") or "",
                    "ready": d.ready, "status": d.status,
                })
            return out
        except Exception:
            return []

    # ---- cockpit session persistence -------------------------------------
    #
    # The TUI persists every cockpit Q&A conversation (store.py) so a returning
    # human can resume; these wrappers expose that to the GUI. Best-effort: a
    # no-op when history persistence is disabled ([history] enabled=false /
    # CC_COPILOT_HISTORY=0), and never raises - a storage failure degrades to
    # in-memory-only, matching the TUI's contract.

    def cockpit_sessions(self, cwd: Optional[str] = None) -> List[dict]:
        """Resumable cockpit conversations (newest first), as JSON-safe headers."""
        if not ST.enabled():
            return []
        try:
            out = []
            for h in ST.list_conversations(cwd):
                d = asdict(h)
                d["ago"] = h.ago()
                out.append(d)
            return out
        except Exception:
            return []

    def cockpit_history(self, cwd: Optional[str] = None,
                        session: Optional[str] = None) -> List[list]:
        """This session's saved cockpit Q&A turns as ``[[role, text], ...]``."""
        path = self._path_or_none(cwd, session)
        if not path or not ST.enabled():
            return []
        try:
            store = ST.Store.open_for(path, enabled=ST.enabled())
            return [[r, t] for r, t in store.load_history()]
        except Exception:
            return []

    def cockpit_record(self, cwd: Optional[str] = None, session: Optional[str] = None, *,
                       question: str, answer: str,
                       backend=None, model=None) -> int:
        """Record a cockpit Q&A turn for this session. Returns the turn count
        after the record, or 0 if persistence is disabled / the record failed."""
        path = self._path_or_none(cwd, session)
        if not path or not ST.enabled():
            return 0
        try:
            store = ST.Store.open_for(path, enabled=True)
            st = S.build(SRC.parse(path))
            store.record_turn(question, answer, st=st, backend=backend, model=model)
            h = store.header()
            return h.turns if h else 0
        except Exception:
            return 0

    def cockpit_forget(self, cwd: Optional[str] = None,
                       session: Optional[str] = None) -> bool:
        """Delete this session's saved cockpit conversation. Returns True if deleted."""
        path = self._path_or_none(cwd, session)
        if not path or not ST.enabled():
            return False
        try:
            return ST.Store.open_for(path, enabled=True).delete()
        except Exception:
            return False

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

    def _path_or_none(self, cwd: Optional[str], session: Optional[str]) -> Optional[str]:
        """Resolve a session path without raising - returns None if nothing
        resolves. Used by the best-effort cockpit-persistence methods."""
        if session and os.path.isfile(session):
            return session
        if not cwd:
            return None
        return self.resolve(cwd, session)