"""Deterministic "what changed since you last looked" — the re-entry view.

Given a transcript and a cutoff (a last-look line, or a duration like ``30m``),
render a cited summary of everything new: your unanswered asks, the agent's new
messages, commands run, failures, files changed, and any status/safety
transition. Every claim cites a ``[L<n>]`` transcript line, same as ``brief``.

Like the rest of the core this is computed by rule — no LLM — and works for any
agent the normalized model covers (Claude Code, Codex, …).
"""

from __future__ import annotations

import os
import re
import subprocess

from . import git_safe as GIT
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import state as S
from .brief import _cite, _dur
from .transcript import Transcript


def _abspath(path: str, cwd: str) -> str:
    if not path:
        return path
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd or "", path))


def _git_worktree(cwd: str):
    """``(is_repo, dirty_abs)`` for ``cwd`` — read-only `git status` of the working
    tree. ``dirty_abs`` is the set of absolute paths with uncommitted changes;
    used to reconcile what the transcript says was edited against what's actually
    still pending in the tree (committed/reverted edits, or out-of-session edits)."""
    if not cwd:
        return False, set()
    try:
        chk = subprocess.run(GIT.argv(cwd, "rev-parse", "--is-inside-work-tree"),
                             capture_output=True, text=True, timeout=2,
                             encoding="utf-8", errors="replace", env=GIT.env())
        if chk.returncode != 0 or chk.stdout.strip() != "true":
            return False, set()
        # --untracked-files=all so a new file isn't collapsed under a `?? dir/`
        # entry; --no-optional-locks keeps this read-only path from touching the
        # index lock. Porcelain paths are relative to the cwd git ran in (-C cwd),
        # the same base the transcript edits use — so both normalize against cwd,
        # not the repo root (cwd may be a subdir of the repo).
        st = subprocess.run(GIT.argv(cwd, "status", "--short", "--untracked-files=all"),
                            capture_output=True, text=True, timeout=2,
                            encoding="utf-8", errors="replace", env=GIT.env())
        if st.returncode != 0:               # status failed (locked index, etc.):
            return False, set()              # don't pretend the tree is clean
    except (OSError, subprocess.TimeoutExpired):
        return False, set()
    dirty = set()
    for line in st.stdout.splitlines():
        s = line[3:].strip()                 # drop the "XY " status prefix
        if " -> " in s:                      # rename: "old -> new"
            s = s.split(" -> ", 1)[1]
        s = s.strip().strip('"')
        if s:
            dirty.add(_abspath(s, cwd))
    return True, dirty


def _reconcile_rows(chg, dirty_abs, cwd, is_repo, all_touched_abs=None):
    """Per-changed-file working-tree status + files dirty outside this session.
    Pure (no IO) so it's directly testable; the git read happens in the caller.
    ``all_touched_abs`` is the abs paths of EVERY file this session edited (not
    just the delta ``chg``), so an earlier still-dirty session edit isn't
    misreported as an out-of-session change."""
    rows = []
    for fc in chg[:10]:
        tag = ""
        if is_repo:
            tag = ("  ● uncommitted" if _abspath(fc.path, cwd) in dirty_abs
                   else "  ✓ committed/reverted since")
        rows.append((fc, tag))
    base = (all_touched_abs if all_touched_abs is not None
            else {_abspath(fc.path, cwd) for fc in chg})
    extra = sorted(dirty_abs - base) if is_repo else []
    return rows, extra


@dataclass
class SinceView:
    cutoff_line: int
    label: str
    new_events: int
    text: str
    nothing_new: bool = True       # True iff the render is the "Nothing new" line
    pending_ask: str = ""          # cited "last ask still unanswered" cue, or ""
    # Structured delta rows (populated by build) for the GUI /diff view - the
    # rendered ``text`` is the Markdown; these are the typed records behind it.
    new_humans: list = field(default_factory=list)        # Record: new user turns
    new_agent: list = field(default_factory=list)         # Record: new agent messages
    new_commands: list = field(default_factory=list)      # Command: new commands
    new_failures: list = field(default_factory=list)      # Failure: new failures
    new_changed_files: list = field(default_factory=list)  # FileChange: files changed
    diff: object = None                                   # State.Diff (transitions + deltas)

    @property
    def has_changes(self) -> bool:
        return self.new_events > 0


_DUR_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.I)


def parse_duration(s: str) -> Optional[float]:
    """``"30m"`` / ``"2h"`` / ``"90s"`` / ``"1d"`` → seconds, or None."""
    m = _DUR_RE.match(s or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def cutoff_line_for_seconds(tr: Transcript, seconds: float) -> int:
    """The line such that records *after* it are within the last ``seconds``.
    A window so large it overflows datetime arithmetic (e.g. ``/since 999999999d``)
    means "since the start" — every record is within it, so the cutoff is 0."""
    try:
        threshold = datetime.now(timezone.utc).astimezone() - timedelta(seconds=seconds)
    except (OverflowError, OSError, ValueError):
        return 0
    cutoff = 0
    for r in tr.records:
        if r.ts is not None and r.ts >= threshold:
            return max(0, r.line - 1)
        if r.ts is not None:
            cutoff = r.line
    return cutoff


def _changed_since(tr: Transcript, st: S.State, cutoff: int):
    """Files whose mutation landed after ``cutoff`` — counting an edit whose
    *result* arrived after the cutoff even if its call was before it.

    state.diff misses that case: a mutation pending at the cutoff is credited in
    both the old prefix (no result yet) and the new state, so the totals match
    and the file is dropped. Derive directly from records instead.
    """
    from .state import _input_path
    from .transcript import MUTATING_TOOLS
    call_by_id = {r.tool_id: r for r in tr.records if r.kind == "tool_call" and r.tool_id}
    result_by_id = {r.tool_id: r for r in tr.records
                    if r.kind == "tool_result" and r.tool_id}
    paths = set()
    for r in tr.records:
        if r.line <= cutoff:
            continue
        if r.kind == "tool_call" and r.tool_name in MUTATING_TOOLS:
            res = result_by_id.get(r.tool_id)
            if res is not None and res.is_error:       # a failed edit changed nothing
                continue
            p = _input_path(r.tool_input)
            if p:
                paths.add(p)
        elif r.kind == "tool_result" and not r.is_error:
            call = call_by_id.get(r.tool_id)
            if call is not None and call.tool_name in MUTATING_TOOLS:
                p = _input_path(call.tool_input)
                if p:
                    paths.add(p)
    changed = [st.files[p] for p in paths if p in st.files]
    changed.sort(key=lambda c: -c.last_line)
    return changed


def _state_upto(tr: Transcript, cutoff_line: int) -> S.State:
    """A State reconstructed from only the records at or before ``cutoff_line``."""
    old_tr = Transcript(
        path=tr.path, session_id=tr.session_id, cwd=tr.cwd,
        git_branch=tr.git_branch, version=tr.version,
        permission_mode=tr.permission_mode, title=tr.title,
        records=[r for r in tr.records if r.line <= cutoff_line],
        raw_lines=cutoff_line,
        first_seen_ts=tr.first_seen_ts,
    )
    if old_tr.records:
        # the last record may be metadata with no ts — use the last real one so
        # the old state's idle/status (and thus the transition shown) is accurate
        old_tr.last_seen_ts = next(
            (r.ts for r in reversed(old_tr.records) if r.ts is not None),
            tr.first_seen_ts)
    return S.build(old_tr)


def build(tr: Transcript, st: S.State, *, since_line: Optional[int] = None,
          seconds: Optional[float] = None, label: str = "last look",
          looked_at: str = "") -> SinceView:
    if since_line is not None:
        cutoff = max(0, int(since_line))
    elif seconds is not None:
        cutoff = cutoff_line_for_seconds(tr, seconds)
    else:
        cutoff = 0

    new_recs = [r for r in tr.records if r.line > cutoff]
    new_humans = [r for r in new_recs if r.kind == "human" and not r.housekeeping
                  and r.text.strip()]
    new_agent = [r for r in new_recs if r.kind == "agent_text" and r.text.strip()]
    # a command counts as "new" if it STARTED or COMPLETED after the cutoff — a
    # command running when you left and finishing while away is new activity.
    new_cmds = [c for c in st.commands
                if c.line > cutoff or (c.result_line and c.result_line > cutoff)]

    old = _state_upto(tr, cutoff)
    d = S.diff(old, st)
    new_fail = d.new_failures               # failures key off the result line — correct
    new_chg = _changed_since(tr, st, cutoff)

    n = (len(new_humans) + len(new_agent) + len(new_cmds)
         + len(new_fail) + len(new_chg))
    # a status/safety transition is shown even with zero counted events (e.g. a new
    # read-only Read flips idle → running) — so the delta isn't empty for recap.
    transition = (d.status_from != d.status_to) or (d.verdict_from != d.verdict_to)
    nothing = (n == 0 and not transition)
    text = _render(label, cutoff, st, d, new_humans, new_agent,
                   new_cmds, new_fail, new_chg, looked_at)
    return SinceView(cutoff_line=cutoff, label=label, new_events=n, text=text,
                     nothing_new=nothing, pending_ask=_pending_ask_line(st),
                     new_humans=new_humans, new_agent=new_agent,
                     new_commands=new_cmds, new_failures=new_fail,
                     new_changed_files=new_chg, diff=d)


def _pending_ask_line(st: S.State) -> str:
    """The cited 'your last ask is still unanswered' cue when the agent owes a
    reply (status awaiting-agent), else ``""``. Exposed on the view so the
    LLM-recap compose path can hoist it ABOVE the narration, not just the raw."""
    if st.status == "awaiting-agent" and st.intents:
        ask = st.intents[-1]
        return (f"⏳ **Your last ask is still unanswered:** {_clip(ask.text, 120)}"
                f"  {_cite(ask.line, ask.hhmm)}")
    return ""


def _clip(s: str, n: int = 160) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _cutoff_hhmm(tr: Transcript, cutoff: int) -> str:
    """Local ``HH:MM`` of the last activity at/before the cutoff — i.e. roughly
    when the returning human last looked. ``""`` when nothing timestamped sits
    before the cutoff (e.g. a whole-session recap from line 0)."""
    seen = ""
    for r in tr.records:
        if r.line > cutoff:
            break
        if r.ts is not None:
            seen = r.hhmm
    return seen


def _away(looked_at: str) -> str:
    """How long the human has been away (now − the stored last-look time), as a
    short duration — the resumption-lag cue. ``""`` if unknown/unparseable."""
    if not looked_at:
        return ""
    try:
        then = datetime.fromisoformat(looked_at)
    except ValueError:
        return ""
    now = datetime.now(timezone.utc).astimezone()
    if then.tzinfo is None:
        then = then.astimezone()
    return _dur(max(0.0, (now - then).total_seconds()))


def _render(label, cutoff, st, d, humans, agent, cmds, fails, chg,
            looked_at="") -> str:
    tr = st.tr
    L = []
    push = L.append
    push(f"# 🛰  cc-copilot — since {label}")
    sid = tr.session_id[:8] if tr.session_id else "?"
    # Time-anchored, consistent with the cockpit's HH:MM convention (was the raw
    # `L0 → now L9` line span). "since <clock time you last looked> · N new lines".
    last = tr.records[-1].line if tr.records else cutoff
    new_lines = max(0, last - cutoff)
    when = _cutoff_hhmm(tr, cutoff)
    since = f"since {when}" if when else ("since start" if cutoff == 0 else "")
    away = _away(looked_at)            # "you were away ~47m" — the resumption cue
    if since and away:
        since += f" (away {away})"
    span = (f"{new_lines} new line{'' if new_lines == 1 else 's'}"
            if new_lines else "")
    tail = "  ·  ".join(p for p in (since, span) if p)
    push(f"`{tr.cwd or '?'}`  ·  session `{sid}…`" + (f"  ·  {tail}" if tail else ""))
    push("")

    # Lead with the suspended decision: if the agent still owes you a reply, that
    # is the first thing a returning human needs (the retrieval cue), even when
    # nothing else changed while you were away.
    cue = _pending_ask_line(st)
    if cue:
        push(cue)
        push("")

    nothing = not (humans or agent or cmds or fails or chg
                   or d.status_from != d.status_to or d.verdict_from != d.verdict_to)
    if nothing:
        push(f"**Nothing new since {label}.** Still "
             f"{st.status} · idle {_dur(st.idle_seconds)}.")
        return "\n".join(L)

    # Headline transition --------------------------------------------------
    if d.status_from and d.status_from != d.status_to:
        push(f"## Status: {d.status_from} → **{d.status_to}**")
    else:
        push(f"## Status: {st.status}  ·  idle {_dur(st.idle_seconds)}")
    if d.verdict_from and d.verdict_from != d.verdict_to:
        push(f"Safety: {d.verdict_from} → **{d.verdict_to}**")
    push("")

    if humans:
        push("## Your asks since then")
        for r in humans[-5:]:
            push(f"- {_clip(r.text)}  {_cite(r.line, r.hhmm)}")
        push("")

    if cmds:
        ok = sum(1 for c in cmds if c.status == "ok")
        bad = sum(1 for c in cmds if c.status == "fail")
        push(f"## Commands  ({len(cmds)} new · {ok} ok · {bad} failed)")
        for c in cmds[-6:]:
            mark = {"ok": "✓", "fail": "✗"}.get(c.status, "·")
            push(f"- {mark} `{_clip(c.cmd, 90)}`  {_cite(c.line, c.hhmm)}")
        push("")

    if fails:
        push(f"## New failures  ({len(fails)})")
        for f in fails[-6:]:
            tgt = f" — `{_clip(f.target, 60)}`" if f.target else ""
            push(f"- {f.tool}{tgt}: {_clip(f.summary, 100)}  {_cite(f.line, f.hhmm)}")
        push("")

    if chg:
        push(f"## Files changed  ({len(chg)})")
        # Reconcile against the REAL working tree: a transcript edit may already be
        # committed/reverted (✓), or still pending (●). And the tree may be dirty
        # from edits this session never made (you / another agent).
        is_repo, dirty = _git_worktree(tr.cwd)
        all_touched = {_abspath(p, tr.cwd) for p in st.files}
        rows, extra = _reconcile_rows(chg, dirty, tr.cwd, is_repo, all_touched)
        for fc, tag in rows:
            push(f"- `{fc.path}` ({fc.edits}e/{fc.writes}w)  "
                 f"{_cite(fc.last_line, fc.last_hhmm)}{tag}")
        if extra:
            names = ", ".join(os.path.basename(p) for p in extra[:6])
            more = f" (+{len(extra) - 6} more)" if len(extra) > 6 else ""
            push(f"- ⚠ also uncommitted, not edited in this session: {names}{more}")
        push("")

    if agent:
        push("## Agent's new words")
        for r in agent[-2:]:
            push(f"> {_clip(r.text, 240)}  {_cite(r.line, r.hhmm)}")
        push("")

    push("---")
    push(f"_{len(humans)} ask(s) · {len(cmds)} command(s) · {len(fails)} failure(s) · "
         f"{len(chg)} file(s) changed since {label}. Every `[L…]` is a transcript line._")
    return "\n".join(L)
