"""Deterministic "what changed since you last looked" — the re-entry view.

Given a transcript and a cutoff (a last-look line, or a duration like ``30m``),
render a cited summary of everything new: your unanswered asks, the agent's new
messages, commands run, failures, files changed, and any status/safety
transition. Every claim cites a ``[L<n>]`` transcript line, same as ``brief``.

Like the rest of the core this is computed by rule — no LLM — and works for any
agent the normalized model covers (Claude Code, Codex, …).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import state as S
from .brief import _cite, _dur
from .transcript import Transcript


@dataclass
class SinceView:
    cutoff_line: int
    label: str
    new_events: int
    text: str

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
    """The line such that records *after* it are within the last ``seconds``."""
    threshold = datetime.now(timezone.utc).astimezone() - timedelta(seconds=seconds)
    cutoff = 0
    for r in tr.records:
        if r.ts is not None and r.ts >= threshold:
            return max(0, r.line - 1)
        if r.ts is not None:
            cutoff = r.line
    return cutoff


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
          seconds: Optional[float] = None, label: str = "last look") -> SinceView:
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
    new_cmds = [c for c in st.commands if c.line > cutoff]

    old = _state_upto(tr, cutoff)
    d = S.diff(old, st)
    new_fail = d.new_failures
    new_chg = d.new_changed

    n = (len(new_humans) + len(new_agent) + len(new_cmds)
         + len(new_fail) + len(new_chg))
    text = _render(label, cutoff, st, d, new_humans, new_agent,
                   new_cmds, new_fail, new_chg)
    return SinceView(cutoff_line=cutoff, label=label, new_events=n, text=text)


def _clip(s: str, n: int = 160) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _render(label, cutoff, st, d, humans, agent, cmds, fails, chg) -> str:
    tr = st.tr
    L = []
    push = L.append
    push(f"# 🛰  cc-copilot — since {label}")
    sid = tr.session_id[:8] if tr.session_id else "?"
    push(f"`{tr.cwd or '?'}`  ·  session `{sid}…`  ·  watching up to L{cutoff} → "
         f"now L{tr.records[-1].line if tr.records else cutoff}")
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
        for fc in chg[:10]:
            push(f"- `{fc.path}` ({fc.edits}e/{fc.writes}w)  {_cite(fc.last_line, fc.last_hhmm)}")
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
