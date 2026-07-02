"""The persistent, parallel, read-only-by-default chat sidecar (`cc-copilot chat`).

Pin to one *other* session and hold an ongoing QA conversation while the copilot
shares the observed session's LIVE timeline: every turn re-parses the (growing)
JSONL, so an answer can never lag the agent. A background thread watches the file
and pushes inline "it just stalled / went off-track" alerts so it feels parallel,
not pull-only.

Read-only by default with respect to the observed agent: the transcript is opened for
reading only, and there is no handle to the observed agent's process. Separate
copilot Q&A history may be persisted under cc-copilot's own state directory.
"""

from __future__ import annotations

import os
import re
import sys
import threading

from . import (sources as SRC, state as S, brief as B, assess as A,
               narrate as N, locate as LOC, store as ST, scope as SC,
               observe as O, context as EC, lastlook as LL, since as SI,
               handoff as HO, collide as CD, scope_groups as SG)

_GLYPH = {"running": "🟢", "stalled": "🔴", "awaiting-agent": "🟡",
          "idle": "⚪", "empty": "∅"}


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _same_file(a: str, b: str) -> bool:
    """samefile() that degrades to a path comparison when a side is missing.

    In history-only mode the observed transcript can be deleted while the
    cockpit keeps its path, so os.path.samefile would raise FileNotFoundError.
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)

_HELP = """commands (LLM-free except questions and explicit /since --recap):
  /brief            full evidence-cited recap
  /observe          attention queue + next human decision
  /now [steer]      recommend the next step (LLM; e.g. /now in spanish; deterministic fallback)
  /goal [steer]     draft a paste-ready agent /goal from agent + project context
  /loop [steer]     draft a paste-ready agent /loop from agent + project context
  /since [when] [steer]  cited delta since you last looked (30m / 2h / 1d; --recap = LLM)
  /handoff [file]   shareable Markdown handoff (brief + what changed)
  /check            safety verdict + friction signals
  /diff             what changed since your last turn
  /refresh          re-read evidence now
  /scope [name]     show or set evidence range: session, multi-session, project
  /status           fleet board — every session in this project, neediest first
  /target           current cockpit target (id, evidence session, scope)
  /sessions         list agent sessions available as evidence
  /here             observe your own current (live) session
  /use <n|id>       change the current evidence session (keeps this cockpit chat)
  /resume           resume another cockpit session
  /new              start a new independent cockpit session  (alias: /new-cockpit)
  /history          alias for /resume; with no args shows this cockpit's turns
  /forget           delete THIS cockpit session's saved resume state
  /rewind [n]       fork from an earlier message (list, or re-ask #n)
  /help             this
  /exit  /quit      leave  (Ctrl-D also works)
anything else → a question answered grounded in the selected read-only evidence + project context (narrator read-only by default)."""

_META_COMMAND_NAMES = {
    "?", "q", "quit", "exit", "help", "brief", "observe", "now", "goal",
    "loop", "since", "handoff", "check", "refresh", "scope", "target",
    "status", "sessions", "here", "use", "new", "new-cockpit", "resume",
    "history", "diff", "forget", "rewind", "clear", "cls", "stop", "cancel",
    "model", "theme", "init", "onboard", "watch",
}


def _looks_like_absolute_path_input(text: str) -> bool:
    text = (text or "").strip()
    if not text.startswith("/") or text == "/":
        return False
    token = text.split(None, 1)[0]
    body = token[1:]
    if not body:
        return False
    # Absolute paths commonly contain another slash, a dotted/hidden segment, or
    # a home-ish marker. Slash commands stay word-like and are checked before
    # this predicate, including commands with hyphenated names.
    if "/" in body or any(ch in body for ch in ".~"):
        return True
    try:
        return os.path.isabs(token) and os.path.exists(token)
    except (OSError, ValueError):
        return False


def _is_meta_command_input(text: str) -> bool:
    """Whether a submitted slash-prefixed line should be handled as a command.

    Keep typo feedback for command-shaped input like ``/session``, but let real
    absolute paths such as ``/data-01/foo`` or ``/Users/me/file`` pass through as
    ordinary user questions.
    """
    text = (text or "").strip()
    if not text.startswith("/"):
        return False
    token = text.split(None, 1)[0]
    name = token[1:].lower()
    if name in _META_COMMAND_NAMES:
        return True
    if _looks_like_absolute_path_input(text):
        return False
    return True


def _dur(sec):
    if sec is None:
        return "?"
    s = int(sec)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600}h"


def _fmt_diff(d) -> str:
    if (d.new_events == 0 and not d.new_failures and not d.new_changed
            and d.status_from == d.status_to):
        return "(no change since last turn)"
    L = [f"since last turn: +{d.new_events} events"]
    if d.status_from != d.status_to:
        L.append(f"  status: {d.status_from or '∅'} → {d.status_to}")
    if d.verdict_from != d.verdict_to:
        L.append(f"  safety: {d.verdict_from or '∅'} → {d.verdict_to}")
    for fc in d.new_changed[:6]:
        L.append(f"  ~ {fc.path} ({fc.total} edit/write) [L{fc.last_line}]")
    for f in d.new_failures[:5]:
        L.append(f"  ⚠ {f.tool} failed [L{f.line}]: {f.summary[:80]}")
    return "\n".join(L)


def _fmt_alert(d) -> str:
    """An alert only fires on a meaningful *transition*, to stay quiet."""
    bits = []
    if d.status_to != d.status_from and d.status_to in ("stalled", "awaiting-agent"):
        bits.append(f"observed session → {d.status_to.upper()}")
    if d.verdict_to == "intervene" and d.verdict_from != "intervene":
        bits.append("safety → INTERVENE")
    if d.new_failures:
        f = d.new_failures[-1]
        bits.append(f"{len(d.new_failures)} new error(s), e.g. {f.tool} [L{f.line}]")
    if not bits:
        return ""
    tail = f"  (+{d.new_events} events)" if d.new_events else ""
    return " · ".join(bits) + tail


def _fmt_conv_list(headers, scope="") -> str:
    """One row per resumable cockpit session (newest first)."""
    if not headers:
        where = f" for {scope}" if scope else ""
        return (f"(no resumable cockpit sessions{where})\n"
                f"  state dir: {ST.state_home()}")
    out = ["resumable cockpit sessions" + (f" — {scope}" if scope else "")
           + f"  ({len(headers)}):"]
    for h in headers:
        gone = "  (transcript gone)" if not h.transcript_present else ""
        proj = os.path.basename(h.cwd) or "?"
        out.append(f"  {h.conv_id[:8]}  {LOC.ago(h.updated):>5} ago  {h.turns:>3}t  "
                   f"{(h.title or '(untitled)')[:40]:<40}  {proj}{gone}")
    return "\n".join(out)


def _clip_words(text: str, n: int = 220) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _goal_context_question(instruction: str = "") -> str:
    base = ("Draft a paste-ready /goal command for the observed coding agent "
            "using the current agent evidence and read-only project context.")
    instruction = (instruction or "").strip()
    if instruction:
        base += f" Human steering for the goal draft: {instruction}"
    return base


def _extract_goal_command(text: str) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line.startswith("/goal"):
            return line
    return ""


def _extract_loop_command(text: str) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line.startswith("/loop"):
            return line
    return ""


def _verification_command(st) -> str:
    checks = []
    needles = ("test", "pytest", "lint", "build", "typecheck", "check", "cargo test",
               "go test", "make")
    for cmd in reversed(getattr(st, "commands", []) or []):
        c = (cmd.cmd or "").strip()
        low = c.lower()
        if c and any(n in low for n in needles):
            checks.append(c)
        if len(checks) >= 2:
            break
    if not checks:
        return ("running the relevant test, build, lint, or reproduction command "
                "for the changed area, or explicitly reporting why local "
                "verification is unavailable")
    if len(checks) == 1:
        return f"rerunning `{checks[0]}`"
    return f"rerunning `{checks[0]}` and `{checks[1]}`"


def _deterministic_goal(st, instruction: str = "") -> str:
    """Small no-model /goal draft from the folded state model."""
    first = getattr(st, "first_intent", None)
    latest = (getattr(st, "intents", []) or [None])[-1]
    first_text = _clip_words(getattr(first, "text", ""), 260)
    latest_text = _clip_words(getattr(latest, "text", ""), 260)
    if first_text and latest_text and first_text.lower() != latest_text.lower():
        outcome = f"finish the original task ({first_text}) while honoring the latest steer ({latest_text})"
    else:
        outcome = latest_text or first_text or "finish the current coding task"

    if instruction:
        outcome += f"; prioritize this steering: {_clip_words(instruction, 220)}"

    constraints = []
    changed = [fc.path for fc in getattr(st, "changed_files", [])[:5]]
    if changed:
        constraints.append("keep changes focused on " + ", ".join(f"`{p}`" for p in changed))
    pending = [t.get("content", "") for t in getattr(st, "todos", [])
               if str(t.get("status", "")).lower() not in ("completed", "done")]
    if pending:
        constraints.append("complete or explicitly close the pending todos: "
                           + "; ".join(_clip_words(t, 90) for t in pending[:4]))
    failures = getattr(st, "failures", []) or []
    if failures:
        f = failures[-1]
        target = f.target or f.summary
        constraints.append("do not declare complete while the latest known failure remains unresolved: "
                           + _clip_words(target, 140))
    if not constraints:
        constraints.append("avoid unrelated refactors and keep the diff scoped to the task")

    objective = (
        f"{outcome}. Verify success by {_verification_command(st)} and by checking "
        "the final diff/artifact against the requested behavior. "
        + " ".join(c[0].upper() + c[1:] + "." for c in constraints if c)
        + " Do not mark complete until verification evidence is visible in the transcript. "
        "If blocked, stop with attempted paths, concrete evidence, the blocker, and the exact user input needed."
    )
    objective = _clip_words(objective, 3600)

    why = []
    if latest is not None and getattr(latest, "line", 0):
        why.append(f"- latest agent-facing intent is at [L{latest.line}]")
    elif first is not None and getattr(first, "line", 0):
        why.append(f"- original agent-facing intent is at [L{first.line}]")
    if getattr(st, "commands", None):
        c = st.commands[-1]
        why.append(f"- recent command evidence includes `{_clip_words(c.cmd, 90)}` [L{c.line}]")
    if changed:
        why.append(f"- current session changed {', '.join('`' + p + '`' for p in changed[:3])}")
    if failures:
        why.append(f"- latest failure evidence is at [L{failures[-1].line}]")
    if not why:
        why.append("- based on the currently selected live session evidence")

    return ("# 🎯 agent goal\n\n"
            "Paste this into the observed agent; cc-copilot does not inject it.\n\n"
            "```text\n"
            f"/goal {objective}\n"
            "```\n\n"
            "## Why this goal\n" + "\n".join(why))


def _loop_context_question(instruction: str = "") -> str:
    base = ("Draft a paste-ready /loop command for the observed coding agent "
            "using the current agent evidence and read-only project context.")
    instruction = (instruction or "").strip()
    if instruction:
        base += f" Human steering for the loop draft: {instruction}"
    return base


def _loop_interval(instruction: str = "") -> str:
    text = (instruction or "").strip().lower()
    m = re.match(r"^(\d+\s*[smhd])\b", text)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    m = re.search(r"\bevery\s+(\d+)\s*(second|seconds|sec|secs|s|minute|minutes|min|mins|m|hour|hours|hr|hrs|h|day|days|d)\b", text)
    if not m:
        return ""
    unit = m.group(2)
    suffix = "s" if unit.startswith(("second", "sec")) or unit == "s" else \
        "m" if unit.startswith(("minute", "min")) or unit == "m" else \
        "h" if unit.startswith(("hour", "hr")) or unit == "h" else "d"
    return f"{m.group(1)}{suffix}"


def _deterministic_loop(st, instruction: str = "") -> str:
    """Small no-model /loop draft from the folded state model."""
    first = getattr(st, "first_intent", None)
    latest = (getattr(st, "intents", []) or [None])[-1]
    first_text = _clip_words(getattr(first, "text", ""), 220)
    latest_text = _clip_words(getattr(latest, "text", ""), 220)
    if latest_text and first_text and latest_text.lower() != first_text.lower():
        task = f"continue the original task ({first_text}) while honoring the latest steer ({latest_text})"
    else:
        task = latest_text or first_text or "continue the current coding task"
    if instruction:
        task += f"; follow this loop steering: {_clip_words(instruction, 220)}"

    interval = _loop_interval(instruction)
    prefix = f"/loop {interval} " if interval else "/loop "

    checks = _verification_command(st)
    recent_cmd = ""
    if getattr(st, "commands", None):
        recent_cmd = _clip_words(st.commands[-1].cmd, 140)
    failure = ""
    if getattr(st, "failures", None):
        f = st.failures[-1]
        failure = _clip_words(f.target or f.summary, 160)
    changed = [fc.path for fc in getattr(st, "changed_files", [])[:4]]
    changed_note = (" Review the focused diff in " + ", ".join(f"`{p}`" for p in changed) + "."
                    if changed else "")
    cmd_note = f" Recent command to re-check when relevant: `{recent_cmd}`." if recent_cmd else ""
    failure_note = f" Treat this known failure as unresolved until fixed: {failure}." if failure else ""

    prompt = (
        f"{task}. On each iteration, inspect the current transcript state, pending "
        f"todos, recent command results, and project diff. If work remains, take the "
        f"smallest relevant next action, then verify by {checks}.{changed_note}"
        f"{cmd_note}{failure_note} If everything is quiet and complete, report that "
        f"in one concise line and do not schedule another wakeup. Do not start "
        f"unrelated initiatives, broaden scope, push, delete, or perform irreversible "
        f"actions unless this conversation already authorized them. Stop and ask the "
        f"human if blocked by credentials, ambiguous requirements, destructive action, "
        f"or repeated failures."
    )
    prompt = _clip_words(prompt, 3600)

    why = []
    if latest is not None and getattr(latest, "line", 0):
        why.append(f"- latest agent-facing intent is at [L{latest.line}]")
    elif first is not None and getattr(first, "line", 0):
        why.append(f"- original agent-facing intent is at [L{first.line}]")
    if getattr(st, "commands", None):
        why.append(f"- recent command evidence includes `{_clip_words(st.commands[-1].cmd, 90)}` [L{st.commands[-1].line}]")
    if failure and getattr(st.failures[-1], "line", 0):
        why.append(f"- latest failure evidence is at [L{st.failures[-1].line}]")
    if changed:
        why.append(f"- current session changed {', '.join('`' + p + '`' for p in changed[:3])}")
    if not why:
        why.append("- based on the currently selected live session evidence")

    return ("# 🔁 agent loop\n\n"
            "Paste this into the observed agent; cc-copilot does not inject it.\n\n"
            "```text\n"
            f"{prefix}{prompt}\n"
            "```\n\n"
            "## Why this loop\n" + "\n".join(why))


def _fleet_rank(status, verdict):
    """Sort key so the sessions that need you float to the top."""
    if status == "stalled" or verdict == "intervene":
        return 0
    if status == "awaiting-agent":
        return 1
    if status == "running":
        return 2 if verdict == "review" else 3
    if verdict == "review":
        return 4            # idle, but had unresolved friction
    if status == "idle":
        return 5
    return 6                # empty


def render_fleet(cwd, limit=10, show_all=False):
    """Fleet board: every work session in the project, neediest first. Shared by
    `cc-copilot status`, the REPL `/status`, and the cockpit `/status`. Returns
    ``(text, session_count)`` so callers can set an exit code on emptiness."""
    all_refs = SRC.list_sessions(cwd, include_own=True)
    refs = [r for r in all_refs if not r.own]
    hidden = len(all_refs) - len(refs)
    if not refs:
        note = f"  ({hidden} cc-copilot helper session(s) hidden)" if hidden else ""
        return (f"(no work sessions for {cwd}){note}\n  dir: {LOC.project_dir_for(cwd)}", 0)
    want = len(refs) if show_all else limit
    rows = []
    for r in refs:
        if len(rows) >= want:
            break          # collected enough parsed rows; a skipped ref never ate a slot
        try:
            tr = SRC.parse(r.path)
            st = S.build(tr)
        except OSError:
            continue       # a session deleted/rotated mid-scan: skip it, try the next ref
        a = A.assess(st)
        sigs = [s for s in a.signals if s.severity in ("alarm", "warn")]
        if sigs:
            head = sigs[0].message + (f" [L{sigs[0].evidence[0]}]" if sigs[0].evidence else "")
        elif st.intents:
            head = st.intents[-1].text
        else:
            head = tr.title or ""
        rows.append((r, st, a, head))
    rows.sort(key=lambda x: (_fleet_rank(x[1].status, x[2].verdict),
                             x[1].idle_seconds if x[1].idle_seconds is not None else 9e9))
    hnote = f", {hidden} helper hidden" if hidden else ""
    out = [f"cc-copilot status — {cwd}  ({len(rows)} of {len(refs)} sessions{hnote})"]
    # Cross-session collision radar: the same file edited by 2+ sessions on
    # DIFFERENT branches — divergence/merge-conflict risk only a cross-agent
    # observer can see. High-signal (cross-branch) only, so the board stays calm.
    try:
        notable = [c for c in CD.collisions(cwd) if c.cross_branch]
    except Exception:
        notable = []
    if notable:
        out.append(f"⚠ {len(notable)} file collision(s) — same file, different branches:")
        for c in notable[:5]:
            who = " · ".join(f"{p.agent} {p.session_id[:8]} ⎇{p.branch or '?'}"
                             for p in c.parties[:3])
            out.append(f"    {c.path}  — {who}")
        if len(notable) > 5:
            out.append(f"    …and {len(notable) - 5} more")
        out.append("")
    multi_agent = len({r.agent for r, *_ in rows}) > 1
    for r, st, a, head in rows:
        g = _GLYPH.get(st.status, "?")
        idle = _dur(st.idle_seconds)
        clip = " ".join((head or "").split())[:56]
        tag = f"{r.agent:<6} " if multi_agent else ""
        # cross-agent fleet awareness: a session that spawned subagents (Claude
        # child transcripts) is a fan-out where the human most easily loses track.
        subs = LOC.subagent_paths(r.path) if r.agent == "claude" else []
        sub = f" +{len(subs)}sub" if subs else ""
        # the other half of the fan-out: a Codex thread forked from another
        # (often with a nickname like "Mill") — show its parentage on one board.
        fk = ""
        if getattr(r, "forked_from", ""):
            fk = f"  ↰{r.forked_from[:8]}" + (f" {r.nickname}" if r.nickname else "")
        elif getattr(r, "nickname", ""):
            fk = f"  ({r.nickname})"
        # which branch each session is on — fleet awareness: who's off main, who
        # shares a branch (potential conflict). Both agents record it.
        br = (st.tr.git_branch or "").strip()
        brcol = f"⎇{br[:16]}  " if br else ""
        out.append(f" {g} {st.status:<13} {a.verdict:<9} {idle:>6} ago  {st.tr.raw_lines:>5}ev{sub}  "
                   f"{tag}{r.session_id[:8]}  {brcol}{clip}{fk}")
        if subs:                          # on-demand board only, so parsing is fine
            out.append("        " + _subagent_rollup(subs))
    return "\n".join(out), len(rows)


_SUB_CAP = 8   # parse at most this many children per parent for the board rollup


def _subagent_rollup(paths) -> str:
    """One indented line summarizing a parent's subagents by status, flagging any
    that need a look. Parses up to _SUB_CAP children (the board is on-demand)."""
    shown = paths[:_SUB_CAP]
    counts, needy = {}, 0
    for p in shown:
        try:
            cst = S.cached_build(p, SRC.parse)
        except OSError:
            continue
        counts[cst.status] = counts.get(cst.status, 0) + 1
        # "needs a look" = the same bar the fleet board uses for the parent:
        # stalled, or a friction verdict (review/intervene) even when idle.
        if cst.status == "stalled" or A.assess(cst).verdict in ("review", "intervene"):
            needy += 1
    order = ["running", "stalled", "awaiting-agent", "idle", "empty"]
    parts = [f"{counts[s]} {s}" for s in order if counts.get(s)]
    line = "↳ subagents: " + (", ".join(parts) if parts else "—")
    if needy:
        line += f"  ⚠ {needy} need a look"
    if len(paths) > len(shown):
        line += f"  (+{len(paths) - len(shown)} more)"
    return line


class ChatSession:
    def __init__(self, path, model=None, backend=None, alerts=True, poll=2,
                 persist=True, scope=None, scope_sessions=None):
        self.path = path
        self.model = model
        self.backend = backend
        requested_scope = SC.normalize(scope) if scope else None
        self.scope = requested_scope or SC.SESSION
        self.scope_sessions = []
        self.poll = max(1, poll)
        self.history = []          # [(role, text)] — restored from the store in _attach
        self.cwd = ""
        self.st = None
        self.prev = None
        self.last_size = -1
        self.last_context_stats = None
        self.last_output_tokens = 0
        self.last_usage = None          # exact backend Usage for the last turn
        self._alerts = alerts
        self._persist = persist and ST.enabled()
        self.store = ST.Store.open_for(path, enabled=self._persist)
        self._rewind_undo = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._alert_size = -1
        self._alert_state = None
        self._thread = None
        self._attach(path, apply_store_state=(requested_scope is None))
        if requested_scope is not None:
            self.scope = requested_scope
        if scope_sessions:
            self.set_scope_sessions(scope_sessions)
        self._persist_state()

    # ---- live state ------------------------------------------------------
    def refresh(self) -> bool:
        """Re-read the session if it grew. Returns True if it changed. Tolerates a
        gone/unreadable transcript (history-only mode) by leaving ``st`` None."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = -1
        if size == self.last_size and self.st is not None:
            return False
        self.last_size = size
        self.prev = self.st
        try:
            self.st = S.cached_build(self.path, SRC.parse)
        except OSError:                    # transcript gone — stay in history-only mode
            self.st = None
            return False
        return True

    def banner(self) -> str:
        st = self.st
        if st is None:
            return f"[scope: {self.scope_label()} · ∅ no live session — transcript gone (history-only)]"
        return (f"[scope: {self.scope_label()} · {_GLYPH.get(st.status, '?')} {st.status} · idle "
                f"{_dur(st.idle_seconds)} · {st.tr.raw_lines} ev · "
                f"safety: {A.assess(st).verdict}]")

    def scope_label(self) -> str:
        if self.scope in (SC.MULTI, SC.PROJECT) and self.scope_sessions:
            return f"{self.scope}:{len(self.scope_sessions)}"
        return self.scope

    def evidence(self, st=None):
        return SC.render_evidence(self.path, self.st if st is None else st,
                                  self.scope, sessions=self.scope_sessions,
                                  project_context=True)

    @staticmethod
    def context_history(history) -> list:
        """History safe to include in model context.

        Failed model calls are tracked in UI history as ``("error", "...")`` so
        rewind can remove them, but their backend error text must not become a
        normal cc-copilot answer. If the latest turn failed, keep its user prompt
        as an unanswered prior request so "try again" still has an antecedent.
        Older failed turns are UI bookkeeping and are omitted from context.
        """
        hist = list(history or [])
        out = []
        i = 0
        while i < len(hist):
            role, text = hist[i]
            if role != "user":
                if role == "assistant":
                    out.append((role, text))
                i += 1
                continue
            nxt = hist[i + 1] if i + 1 < len(hist) else None
            if nxt and nxt[0] == "error":
                if i + 2 >= len(hist):
                    out.append(("user", text))
                i += 2
                continue
            out.append((role, text))
            if nxt and nxt[0] == "assistant":
                out.append(nxt)
                i += 2
            else:
                i += 1
        return out

    @staticmethod
    def stored_turn_count_before(history, message_index: int) -> int:
        """How many durable successful turns precede a UI message index.

        UI history may contain failed ``user/error`` pairs that are intentionally
        not persisted as store turns. Rewind truncation must count only
        successful ``user/assistant`` pairs before the selected message.
        """
        hist = list(history or [])
        limit = max(0, int(message_index or 0))
        count = 0
        for k in range(limit):
            j = 2 * k + 1
            if j < len(hist) and hist[j][0] == "assistant":
                count += 1
        return count

    def answer_context(self, q: str, history=None, st=None):
        hist = self.context_history(self.history if history is None else history)
        memory_text = ""
        if self.store.enabled:
            memory_text, hist = self.store.compact_memory(
                hist, max_raw_chars=EC.chat_history_budget_chars())
        return EC.build(self.path, self.st if st is None else st, self.scope,
                        sessions=self.scope_sessions, question=q,
                        history=hist, project_context=True,
                        memory_text=memory_text)

    def _finalize_turn(self, q: str, txt: str, usage=None):
        """Single completion site for a turn: tokens, in-memory history, and the
        durable store write. Only ever called with a COMPLETE answer — a stream
        that dies mid-way must not reach here (partials are never persisted)."""
        self._rewind_undo = None
        self.last_usage = usage
        exact_out = getattr(usage, "exact", False) and getattr(usage, "output_tokens", 0)
        self.last_output_tokens = (usage.output_tokens if exact_out
                                   else EC.estimate_tokens(txt))
        self.history.append(("user", q))
        self.history.append(("assistant", txt))
        # durable copilot history (best-effort; never breaks the answer)
        self.store.scope = self.scope
        self.store.scope_sessions = list(self.scope_sessions)
        self.store.record_turn(q, txt, st=self.st, backend=self.backend,
                               model=self.model,
                               usage=(usage.as_dict() if usage else None))

    def record_error_turn(self, q: str, err: str) -> None:
        """Keep a failed prompt rewindable without treating the backend error as
        a successful assistant answer or writing it to durable history."""
        q = str(q or "").strip()
        err = str(err or "").strip()
        if not q or not err:
            return
        self._rewind_undo = None
        self.last_usage = None
        self.last_output_tokens = 0
        self.history.append(("user", q))
        self.history.append(("error", err))

    def record_generated_turn(self, q: str, txt: str, usage=None) -> None:
        """Record a generated command result as conversation context.

        Commands like /goal and /loop produce artifacts the next normal question
        may reasonably revise ("make that shorter", "add retries"). Observational
        commands stay out of chat continuity; this helper is intentionally opt-in.
        """
        q = str(q or "").strip()
        txt = str(txt or "")
        if not q or not txt or self.st is None:
            return
        self._finalize_turn(q, txt, usage=usage)

    def answer(self, q: str) -> str:
        self.refresh()
        ctx = self.answer_context(q, history=self.history)
        self.last_context_stats = ctx.stats
        self.last_output_tokens = 0
        txt = N.chat_brief(ctx.text, [], q, model=self.model, backend=self.backend)
        self._finalize_turn(q, txt)
        return txt

    def answer_stream(self, q: str):
        """Generator sibling of :func:`answer`: yields answer chunks as the
        backend produces them, then finalizes (history + durable store + exact
        usage) only after the stream completed. An error mid-stream propagates
        out of the loop and nothing is recorded."""
        self.refresh()
        ctx = self.answer_context(q, history=self.history)
        self.last_context_stats = ctx.stats
        self.last_output_tokens = 0
        h = N.chat_brief_stream(ctx.text, [], q, model=self.model, backend=self.backend)
        for chunk in h:
            yield chunk
        if h.text:
            self._finalize_turn(q, h.text, h.usage)

    def meta(self, cmd: str):
        """Handle a /command. Returns text to print, or False to exit."""
        c = cmd.strip().lower()
        if c in ("/exit", "/quit"):
            return False
        self.refresh()
        if self.st is None and c in ("/check", "/diff"):
            return "(no live session — transcript gone; history-only view)"
        if c == "/help":
            return _HELP
        if c == "/brief":
            return self.evidence().text
        if c == "/observe":
            return O.render(self.path, self.st, self.scope, sessions=self.scope_sessions)
        if c == "/now" or c.startswith("/now "):
            return self._now(cmd.strip()[4:].strip())
        if c == "/goal" or c.startswith("/goal "):
            out = self._goal(cmd.strip()[5:].strip())
            self.record_generated_turn(cmd.strip(), out)
            return out
        if c == "/loop" or c.startswith("/loop "):
            out = self._loop(cmd.strip()[5:].strip())
            self.record_generated_turn(cmd.strip(), out)
            return out
        if c == "/since" or c.startswith("/since "):
            return self._since(cmd.strip()[6:].strip())
        if c == "/handoff" or c.startswith("/handoff "):
            return self._handoff(cmd.strip()[8:].strip())
        if c == "/check":
            return B.render_check(self.st) if self.scope == SC.SESSION else self.evidence().text
        if c == "/refresh":
            return self.banner() + "  (refreshed)"
        if c == "/scope" or c.startswith("/scope "):
            return self._scope(cmd.strip()[6:].strip())
        if c == "/target":
            return f"cockpit: {self.store.conv_id}\ntarget: {self.path}\nevidence: {self.scope_label()}\n{self.banner()}"
        if c == "/status":
            return render_fleet(self.cwd or os.getcwd())[0]
        if c == "/sessions":
            return self._list_sessions()
        if c == "/here":
            return self._switch_here()
        if c.startswith("/use"):
            return self._switch(cmd.strip()[4:].strip())
        if c in ("/new", "/new-cockpit"):
            return self.new_cockpit()
        if c == "/resume" or c.startswith("/resume "):
            return self._resume_list(cmd.strip()[7:].strip())
        if c == "/history" or c.startswith("/history "):
            arg = c[8:].strip()
            if not arg:
                if not self.history:
                    return "(no turns yet — `/resume` lists resumable cockpit sessions)"
                return "\n".join(
                    ("you> " if r == "user" else "err> " if r == "error" else "cc > ")
                    + t[:200]
                    for r, t in self.history)
            if arg in ("all", "*", "this", "project"):
                if not self.store.enabled:
                    return "(history is off — --no-persist or [history] enabled=false)"
                if arg in ("all", "*"):
                    return _fmt_conv_list(ST.list_conversations(None), "all projects")
                cwd = self.cwd or os.getcwd()
                return _fmt_conv_list(ST.list_conversations(cwd), cwd)
            return self._resume_list(arg)
        if c == "/diff":
            return _fmt_diff(S.diff(self.prev, self.st))
        if c == "/forget":
            if not self.store.enabled:
                return "(history is off — nothing saved to forget)"
            self.store.delete()
            self.history = []
            self._rewind_undo = None
            return "forgot this conversation's saved history"
        if c == "/rewind" or c.startswith("/rewind "):
            arg = c[7:].strip()
            if arg in ("undo", "revert"):
                return self.rewind_undo()
            qs = [t for r, t in self.history if r == "user"]
            if not qs:
                if self._rewind_undo:
                    return "rewind undo available: `/rewind undo`"
                return "(nothing to rewind — no questions yet)"
            if not arg.isdigit():
                undo = "  undo. restore the last rewind" if self._rewind_undo else ""
                lines = ["rewind to which message?  `/rewind <n>` re-asks it:"]
                if undo:
                    lines.append(undo)
                lines += [f"  {i}. {q[:60]}" for i, q in enumerate(qs, 1)]
                return "\n".join(lines)
            k = int(arg) - 1
            if not (0 <= k < len(qs)):
                return f"no message #{arg} (have 1–{len(qs)})"
            question = qs[k]
            self._save_rewind_undo()
            self.history = self.history[:2 * k]
            self.store.truncate(self.stored_turn_count_before(self._rewind_undo["history"], k))
            return (f"rewound to before message #{k + 1}. Ask it again, edited as you like:\n"
                    f"  {question}\n"
                    "undo: /rewind undo")
        return f"unknown command {cmd!r} — try /help"

    def _save_rewind_undo(self) -> None:
        self._rewind_undo = {
            "history": list(self.history),
            "snapshot": self.store.snapshot(),
        }

    def rewind_undo(self) -> str:
        undo = self._rewind_undo
        if not undo:
            return "(nothing to undo — no reversible rewind is pending)"
        hist = list(undo.get("history") or [])
        snap = undo.get("snapshot")
        if snap is not None and not self.store.restore_snapshot(snap):
            return "(could not restore the previous rewind snapshot)"
        self.history = hist
        self._rewind_undo = None
        return "restored the conversation to before the last rewind"

    def _scope_groups_text(self) -> str:
        groups = SG.list_groups()
        if not groups:
            return "saved scope groups: none\nusage: /scope save <name>"
        rows = ["saved scope groups:"]
        for g in groups:
            sel = f":{len(g.scope_sessions)}" if g.scope_sessions else ""
            rows.append(f"  {g.name} → {g.scope}{sel}")
        rows.append("usage: /scope load <name>  ·  /scope delete <name>")
        return "\n".join(rows)

    def _apply_scope_group(self, group) -> None:
        self.scope = group.scope
        if self.scope == SC.SESSION:
            self.scope_sessions = []
        elif group.scope_sessions:
            self.set_scope_sessions(group.scope_sessions)
        else:
            self.scope_sessions = []
        self._persist_state()

    def _scope(self, arg):
        if not arg:
            selected = ("selected sessions: " + ", ".join(s[:8] for s in self.scope_sessions)
                        if self.scope_sessions else "selected sessions: all")
            return ("scope: " + self.scope_label() + "\n"
                    + selected + "\n"
                    "available: session, multi-session, project\n"
                    "usage: /scope <session|multi-session|project> [session-id|prefix ...]\n"
                    "       /scope all   # clear the selected subset\n"
                    "       /scope save|load|delete <name>  ·  /scope groups")
        toks = arg.split()
        sub = toks[0].lower() if toks else ""
        if sub in ("groups", "list", "ls"):
            return self._scope_groups_text()
        if sub == "save":
            if len(toks) < 2:
                return "usage: /scope save <name>"
            try:
                g = SG.save(toks[1], self.scope, self.scope_sessions)
            except ValueError as e:
                return str(e)
            sel = f":{len(g.scope_sessions)}" if g.scope_sessions else ""
            return f"saved scope group {g.name} → {g.scope}{sel}"
        if sub in ("load", "use"):
            if len(toks) < 2:
                return "usage: /scope load <name>"
            try:
                g = SG.get(toks[1])
            except ValueError as e:
                return str(e)
            if g is None:
                return f"no saved scope group {toks[1]!r}"
            try:
                self._apply_scope_group(g)
            except ValueError as e:
                return str(e)
            return f"scope group {g.name} → {self.scope_label()}\n{self.banner()}"
        if sub in ("delete", "del", "rm"):
            if len(toks) < 2:
                return "usage: /scope delete <name>"
            try:
                ok = SG.delete(toks[1])
            except ValueError as e:
                return str(e)
            return (f"deleted scope group {toks[1]}"
                    if ok else f"no saved scope group {toks[1]!r}")
        if len(toks) == 1 and toks[0].lower() in ("all", "*", "clear", "reset"):
            self.scope_sessions = []
            self._persist_state()
            return f"scope → {self.scope_label()}\n{self.banner()}"
        try:
            new_scope = SC.normalize(toks[0])
            selectors = toks[1:]
        except ValueError as e:
            if self.scope in (SC.MULTI, SC.PROJECT):
                new_scope = self.scope
                selectors = toks
            else:
                return str(e)
        self.scope = new_scope
        if self.scope == SC.SESSION:
            self.scope_sessions = []
        elif not selectors or selectors in (["all"], ["*"]):
            self.scope_sessions = []
        else:
            try:
                self.set_scope_sessions(selectors)
            except ValueError as e:
                return str(e)
        self._persist_state()
        return f"scope → {self.scope_label()}\n{self.banner()}"

    def set_scope_sessions(self, selectors) -> None:
        refs = SC.resolve_session_refs(self.path, SC.parse_selectors(selectors))
        self.scope_sessions = [r.session_id for r in refs]
        self._persist_state()

    # ---- re-entry: last-look, /since, /handoff ---------------------------
    def _lastlook_key(self) -> str:
        sid = getattr(getattr(self, "st", None), "tr", None)
        sid = getattr(sid, "session_id", "") if sid is not None else ""
        return LL.key_for(sid, self.path)

    def _cur_line(self):
        tr = getattr(self.st, "tr", None)
        recs = getattr(tr, "records", []) if tr is not None else []
        return (recs[-1].line if recs else 0,
                recs[-1].raw_ts if recs else "")

    def mark_lastlook(self) -> None:
        """Record that the human has now seen everything up to the live tail."""
        line, ts = self._cur_line()
        LL.mark(self._lastlook_key(), line, ts, _now_iso())

    def since_summary(self):
        """A SinceView vs the stored last-look marker, or None if no marker/state."""
        if self.st is None:
            return None
        mark = LL.get(self._lastlook_key())
        if mark is None:
            return None
        return SI.build(self.st.tr, self.st, since_line=int(mark.get("line", 0) or 0),
                        label="last look", looked_at=mark.get("looked_at", ""))

    @staticmethod
    def _split_since_arg(arg: str):
        """Separate an optional leading window token (a duration like ``30m`` /
        ``2h`` / ``1d`` or a ``last-look`` keyword) from a trailing free-text
        instruction, e.g. ``2h in spanish`` → ``("2h", "in spanish", False)`` and
        ``in spanish`` → ``("", "in spanish", False)`` (window defaults to
        last-look). ``--raw`` stays with the window part so :meth:`_since_view`
        still sees it; ``--recap`` is consumed here because it controls whether a
        backend is allowed to see the cited delta."""
        toks = [t for t in (arg or "").split() if t]
        raw = [t for t in toks if t == "--raw"]
        recap = "--recap" in toks
        rest = [t for t in toks if t not in ("--raw", "--recap")]
        window = []
        if rest and (rest[0].lower() in ("last-look", "lastlook", "last")
                     or SI.parse_duration(rest[0]) is not None):
            window = [rest.pop(0)]
        return " ".join(raw + window).strip(), " ".join(rest).strip(), recap

    def _since(self, arg: str):
        """Sync entry (REPL / CLI): the recap-or-raw result as one string. The TUI
        uses :meth:`_since_view` + :meth:`_compose_since` so the model call can run
        off the UI thread."""
        window_arg, instruction, recap = self._split_since_arg(arg)
        res = self._since_view(window_arg)
        if isinstance(res, str):
            return res
        view, raw, commit = res
        out = self._since_finish(view, raw, instruction, recap=recap)
        commit()                                   # shown now → advance the marker
        return out

    def _since_view(self, arg: str):
        """Deterministic half: parse ``arg`` and build the SinceView. Returns
        ``(view, raw, commit)`` or an edge-case message string. ``--raw`` forces
        the cited delta with no model call. ``commit`` advances the last-look
        marker and MUST be called only once the recap/evidence is actually shown —
        the TUI may drop an async recap after an evidence switch, and consuming the
        marker before that would silently lose the delta."""
        if self.st is None:
            return "(no live session — transcript gone; nothing to diff)"
        toks = [t for t in (arg or "").split() if t]
        raw = "--raw" in toks
        toks = [t for t in toks if t != "--raw"]
        when = " ".join(toks).strip().lower() or "last-look"
        line, ts = self._cur_line()
        key = self._lastlook_key()
        commit = lambda: None                      # durations don't move the marker
        if when in ("last-look", "lastlook", "last"):
            if not LL.enabled():
                return ("last-look tracking is off (persistence disabled). "
                        "Try `/since 30m` for a time window.")
            mark = LL.get(key)
            if mark is None:
                LL.mark(key, line, ts, _now_iso())
                return (f"No last-look mark yet — recorded your current position (L{line}). "
                        f"Run /since again after the agent works, or `/since 30m` for a window.")
            view = SI.build(self.st.tr, self.st, since_line=int(mark.get("line", 0) or 0),
                            label="last look", looked_at=mark.get("looked_at", ""))

            def commit():                          # consume only on render; never
                LL.advance(key, line, ts, _now_iso())   # rewind past a concurrent mark
        else:
            secs = SI.parse_duration(when)
            if secs is None:
                return f"unknown time {when!r} — use 'last-look' or a duration like 30m / 2h / 1d"
            view = SI.build(self.st.tr, self.st, seconds=secs, label=when)
        return (view, raw, commit)

    def _since_finish(self, view, raw: bool, instruction: str = "",
                      recap: bool = False) -> str:
        """Return the deterministic delta unless ``--recap`` explicitly allows a
        backend recap."""
        if raw or not recap or view.nothing_new or not N.available(self.backend):
            return view.text
        try:
            recap = N.recap_since(view.text, model=self.model, backend=self.backend,
                                  instruction=instruction)
        except Exception as e:
            return view.text + f"\n\n> _recap unavailable ({e}); evidence shown above._"
        return self._compose_since(recap, view)

    @staticmethod
    def _compose_since(recap: str, view) -> str:
        """Narrative on top, the deterministic cited delta beneath it (minus its
        own title, since the recap heading replaces it)."""
        body = view.text.split("\n", 1)[1] if view.text.startswith("#") else view.text
        # hoist the suspended-ask cue ABOVE the narration so it leads the recap
        # path too (not just --raw / no-backend / nothing-new).
        lead = f"{view.pending_ask}\n\n" if getattr(view, "pending_ask", "") else ""
        return (f"# 🛰  recap — since {view.label}\n\n{lead}{recap.strip()}\n\n"
                f"---\n_evidence — every `[L…]` is a transcript line:_\n{body}")

    # ---- /now: recommend the next step from the completed work --------------
    def _now(self, instruction: str = "") -> str:
        """What to do next: an LLM recommendation grounded in the evidence, with a
        deterministic next-step (the observer's ranked decision) as the always-true
        fallback when no backend is available or the recap fails. ``instruction``
        is an optional free-text steer (`/now in spanish`) for the recommendation."""
        if self.st is None:
            return "(no live session — transcript gone; nothing to recommend)"
        det = O.next_step(self.path, self.st, self.scope, sessions=self.scope_sessions)
        if not N.available(self.backend):
            return det
        try:
            rec = N.next_step_brief(self.evidence().text, model=self.model,
                                    backend=self.backend, instruction=instruction)
        except Exception as e:
            return det + f"\n\n> _next-step recap unavailable ({e}); deterministic suggestion above._"
        return self._compose_now(rec, det)

    @staticmethod
    def _compose_now(rec: str, det: str) -> str:
        """LLM recommendation on top, the deterministic next-step beneath it as a
        grounded anchor (its first line — the primary ranked decision)."""
        body = f"# 🧭 next step\n\n{rec.strip()}"
        foot = det.splitlines()[0].strip() if det else ""
        if foot:
            body += f"\n\n---\n_deterministic next-step:_ {foot}"
        return body

    # ---- /goal: draft a persistent objective for the observed agent ---------
    def _goal(self, instruction: str = "", raw: bool = False) -> str:
        """Draft a paste-ready agent ``/goal`` command from observable session
        evidence plus read-only project context. The cockpit does not inject:
        this never writes into the observed agent session (read-only by default
        with respect to the observed agent)."""
        if self.st is None:
            return "(no live session — transcript gone; nothing to turn into a goal)"
        det = _deterministic_goal(self.st, instruction)
        if raw or not N.available(self.backend):
            return det
        question = _goal_context_question(instruction)
        try:
            ctx = self.answer_context(question, history=self.history)
            self.last_context_stats = ctx.stats
            rec = N.goal_brief(ctx.text, model=self.model, backend=self.backend,
                               instruction=instruction)
        except Exception as e:
            return det + f"\n\n> _goal draft unavailable ({e}); deterministic draft above._"
        return self._compose_goal(rec, det)

    @staticmethod
    def _compose_goal(rec: str, det: str) -> str:
        body = f"# 🎯 agent goal\n\n{rec.strip()}"
        fallback = _extract_goal_command(det)
        if fallback:
            body += f"\n\n---\n_deterministic fallback:_\n\n```text\n{fallback}\n```"
        return body

    # ---- /loop: draft a recurring agent prompt ------------------------------
    def _loop(self, instruction: str = "", raw: bool = False) -> str:
        """Draft a paste-ready agent ``/loop`` command from observable session
        evidence plus read-only project context. The cockpit does not inject:
        this never schedules or injects anything into the observed agent
        (read-only by default with respect to the observed agent)."""
        if self.st is None:
            return "(no live session — transcript gone; nothing to turn into a loop)"
        det = _deterministic_loop(self.st, instruction)
        if raw or not N.available(self.backend):
            return det
        question = _loop_context_question(instruction)
        try:
            ctx = self.answer_context(question, history=self.history)
            self.last_context_stats = ctx.stats
            rec = N.loop_brief(ctx.text, model=self.model, backend=self.backend,
                               instruction=instruction)
        except Exception as e:
            return det + f"\n\n> _loop draft unavailable ({e}); deterministic draft above._"
        return self._compose_loop(rec, det)

    @staticmethod
    def _compose_loop(rec: str, det: str) -> str:
        body = f"# 🔁 agent loop\n\n{rec.strip()}"
        fallback = _extract_loop_command(det)
        if fallback:
            body += f"\n\n---\n_deterministic fallback:_\n\n```text\n{fallback}\n```"
        return body

    def _handoff(self, arg: str):
        if self.st is None:
            return "(no live session — transcript gone; nothing to hand off)"
        agent = SRC.source_for_path(self.path).name
        import time as _t
        md = HO.render(self.st, agent=agent, generated_at=_t.strftime("%Y-%m-%d %H:%M"),
                       since_view=self.since_summary())
        out = (arg or "").strip()
        if out:
            try:
                with open(out, "w", encoding="utf-8") as f:
                    f.write(md + "\n")
                return f"wrote handoff → {out}  ({len(md.splitlines())} lines)"
            except OSError as e:
                return f"could not write {out}: {e}"
        return md

    # ---- session switching (select among multiple sessions) --------------
    def _siblings(self):
        # Agent-aware project discovery: Claude Code co-located siblings plus any
        # Codex sessions for the same project cwd. Own narration sessions are
        # hidden (the anchor is always kept). This is the picker path, so inject
        # the human's live session even if it's in another project.
        refs = SC._candidate_refs(self.path, inject_current=True)
        self._session_refs = refs
        self._listing = [r.path for r in refs]
        return self._listing

    def sibling_refs(self):
        """Public list of sibling sessions with title metadata."""
        self._siblings()
        return getattr(self, "_session_refs", [])

    def _list_sessions(self):
        refs = self.sibling_refs()
        out = ["agent sessions in this project (newest first — `/use <n|id>` changes evidence):"]
        for i, r in enumerate(refs, 1):
            p = r.path
            cur = "*" if _same_file(p, self.path) else " "
            title = (r.title or "(untitled)")[:50]
            out.append(f" {cur}{i:>2}. {title:<50}  {r.session_id[:8]}  {r.size // 1024:>6} KB")
        return "\n".join(out)

    def _switch(self, arg):
        if not arg:
            return "usage: /use <number|session-id|prefix>  (see /sessions)"
        self._siblings()
        refs = getattr(self, "_session_refs", [])
        target = None
        if arg.isascii() and arg.isdigit():
            i = int(arg) - 1
            if 0 <= i < len(refs):
                target = refs[i].path
        if target is None:
            for r in refs:
                if r.session_id == arg or r.session_id.startswith(arg):
                    target = r.path
                    break
        if target is None:
            return f"no session matching {arg!r} — try /sessions"
        if _same_file(target, self.path):
            return "already attached to that session"
        self.switch_path(target)
        return (f"evidence session → {os.path.basename(target)[:-6][:8]} "
                f"(cockpit chat kept)\n{self.banner()}")

    def switch_to_here(self):
        """Point at the live session as a *single* session (resetting any wider
        scope, whose selectors belong to the old anchor's project). Returns the
        path, or None if there's no detectable current session."""
        p = SRC.current_session_path()
        if not p:
            return None
        self.scope = SC.SESSION
        self.scope_sessions = []
        self.switch_path(p)        # persists path + the reset scope
        return p

    def _switch_here(self):
        """Switch to observing the session cc-copilot is running inside of."""
        cur = SRC.current_session_path()
        if not cur:
            return ("no current session detected — run cc-copilot inside a live "
                    "Claude Code or Codex session.")
        if os.path.abspath(cur) == os.path.abspath(self.path):
            return "already observing your live session"
        p = self.switch_to_here()
        if not p:      # the live session vanished between the two detect calls
            return ("no current session detected — run cc-copilot inside a live "
                    "Claude Code or Codex session.")
        return (f"now observing your live session → "
                f"{os.path.basename(p)[:-6][:8]}\n{self.banner()}")

    def switch_path(self, path):
        """Change the evidence target while keeping this Cockpit session."""
        self._rewind_undo = None
        self._attach(path, load_store=False)
        self._persist_state()

    def _attach(self, path, load_store=True, apply_store_state=True):
        """Point at ``path``: reset live state, re-open the store for it, and
        load any persisted copilot history. Survives a missing transcript."""
        self.path = path
        self.st = self.prev = None
        self.last_size = -1
        self._alert_state = None
        self._alert_size = -1
        self.refresh()                       # st may stay None if the file is gone
        tr = getattr(self.st, "tr", None)
        if load_store:
            self.store = ST.Store.open_for(path, enabled=self._persist, tr=tr)
            h = self.store.header()
            if h is not None:
                self.store.apply_header(h)
                if apply_store_state:
                    try:
                        self.scope = SC.normalize(h.scope)
                    except ValueError:
                        self.scope = SC.SESSION
                    self.scope_sessions = list(h.scope_sessions or [])
            self.history = self.store.load_history()
        else:
            self.store.transcript = os.path.abspath(path) if path else ""
            if tr is not None:
                self.store.session_id = getattr(tr, "session_id", "") or self.store.session_id
                self.store.cwd = getattr(tr, "cwd", "") or self.store.cwd
                self.store.title = getattr(tr, "title", "") or self.store.title
        self.cwd = (getattr(tr, "cwd", "") or SRC.read_cwd(path) or "")

    def attach_conv(self, header) -> bool:
        """Attach by a stored conversation header (from /history). Returns True
        for a live re-attach, False when the transcript is gone (history-only)."""
        self._rewind_undo = None
        self.store = ST.Store(header.conv_id, enabled=self._persist)
        self.store.apply_header(header)
        if header.transcript and os.path.isfile(header.transcript):
            self._attach(header.transcript, load_store=False)
            self.history = self.store.load_history()
            try:
                self.scope = SC.normalize(header.scope)
            except ValueError:
                self.scope = SC.SESSION
            self.scope_sessions = list(header.scope_sessions or [])
            self.cwd = header.cwd or self.cwd
            return True
        self.path = header.transcript
        self.st = self.prev = None
        self.last_size = -1
        try:
            self.scope = SC.normalize(header.scope)
        except ValueError:
            self.scope = SC.SESSION
        self.scope_sessions = list(header.scope_sessions or [])
        self.history = self.store.load_history()
        self.cwd = header.cwd
        return False

    def _persist_state(self) -> None:
        self.store.scope = self.scope
        self.store.scope_sessions = list(self.scope_sessions)
        self.store.transcript = os.path.abspath(self.path) if self.path else self.store.transcript
        self.store.record_state(self.st, scope=self.scope, scope_sessions=self.scope_sessions,
                                backend=self.backend, model=self.model)

    def new_cockpit(self) -> str:
        self.store = ST.Store.new_for(self.path, enabled=self._persist, tr=getattr(self.st, "tr", None))
        self.history = []
        self._rewind_undo = None
        self._persist_state()
        return f"new cockpit session → {self.store.conv_id}\n{self.banner()}"

    def _resume_list(self, arg="") -> str:
        if not self.store.enabled:
            return "(resume is off — --no-persist or [history] enabled=false)"
        headers = ST.list_conversations(None if arg in ("all", "*") else (self.cwd or os.getcwd()))
        return _fmt_conv_list(headers, "resumable cockpit sessions")

    def siblings(self):
        """Public list of sibling session paths (newest first), own-filtered."""
        return self._siblings()

    # ---- background alerts (read-only, advisory) -------------------------
    def _start_alerts(self):
        if not self._alerts:
            return
        self._alert_size = self.last_size
        self._alert_state = self.st
        self._thread = threading.Thread(target=self._alert_loop, daemon=True)
        self._thread.start()

    def _alert_loop(self):
        while not self._stop.wait(self.poll):
            try:
                size = os.path.getsize(self.path)
            except OSError:
                continue
            if size == self._alert_size:
                continue
            self._alert_size = size
            try:
                st = S.cached_build(self.path, SRC.parse)
            except Exception:
                continue
            msg = _fmt_alert(S.diff(self._alert_state, st))
            self._alert_state = st
            if msg:
                with self._lock:
                    sys.stdout.write("\n🔔 " + msg + "\nyou> ")
                    sys.stdout.flush()

    # ---- REPL ------------------------------------------------------------
    def loop(self):
        try:
            import readline  # noqa: F401  (enables history/arrow keys if present)
        except Exception:
            pass
        self.refresh()
        print(f"🛰  cc-copilot chat — cockpit {self.store.conv_id[:12]}")
        print(self.banner())
        have_llm = N.available(self.backend)
        print(f"backend: {N.backend_name(self.backend)}")
        if not have_llm:
            sys.stderr.write("# backend unavailable; questions need one "
                             "(`cc-copilot backends`). /brief /check /diff still work.\n")
        print("ask a question, or /help.  Ctrl-D to exit.\n")
        self._start_alerts()
        try:
            while True:
                try:
                    line = input("you> ").strip()
                except EOFError:
                    print()
                    break
                if not line:
                    continue
                if _is_meta_command_input(line):
                    out = self.meta(line)
                    if out is False:
                        break
                    with self._lock:
                        print(out + "\n")
                    continue
                if not have_llm:
                    with self._lock:
                        print("# no LLM backend — set CC_COPILOT_LLM_CMD or "
                              "install the claude CLI.\n")
                    continue
                with self._lock:
                    print("…", flush=True)
                # stream the answer as it arrives; the lock is held for the whole
                # stream so background alerts queue up and print after, never
                # splicing into the middle of the answer text.
                got_any = False
                try:
                    with self._lock:
                        for chunk in self.answer_stream(line):
                            if not got_any:
                                sys.stdout.write(self.banner() + "\n")
                                got_any = True
                            sys.stdout.write(chunk)
                            sys.stdout.flush()
                        if got_any:
                            sys.stdout.write("\n\n")
                            sys.stdout.flush()
                except Exception as e:
                    if not got_any:
                        self.record_error_turn(line, f"# error: {e}")
                    with self._lock:
                        print(("\n" if got_any else "") + f"# error: {e}\n")
                    continue
        finally:
            self._stop.set()
