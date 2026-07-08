"""Render a deterministic, evidence-cited brief from a State.

Every load-bearing line ends with a citation like ``[L142 21:26]`` pointing at
the JSONL line it was derived from. This is the whole point: the brief is a
*reading* of the ledger, not a story about it. If a fact has no citation, it
isn't in the brief.
"""

from __future__ import annotations

from .state import State
from .assess import assess


# ---- formatting helpers -------------------------------------------------

def _dur(seconds) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600}h"


def _cite(line: int, hhmm: str = "") -> str:
    return f"[L{line}{(' ' + hhmm) if hhmm else ''}]"


_STATUS_LABEL = {
    "running": "🟢 RUNNING — agent mid-turn (worked recently, no closing message yet)",
    "stalled": "🔴 STALLED — no closing message after its last action (interrupted or stuck)",
    "awaiting-agent": "🟡 AWAITING AGENT — you spoke last; it hasn't replied yet",
    "idle": "⚪ IDLE — agent gave a closing message (your move)",
    "empty": "∅ NO AGENT ACTIVITY — no substantive turns (e.g. local commands only)",
}


# ---- main renderer ------------------------------------------------------

def render(st: State, max_files: int = 12, max_cmds: int = 6) -> str:
    tr = st.tr
    L = []
    push = L.append

    # Header ---------------------------------------------------------------
    title = tr.title or (st.intents[-1].text[:60] if st.intents else "(untitled)")
    push(f"# 🛰  cc-copilot brief — {_oneline(title, 70)}")
    meta = [f"`{tr.cwd or '?'}`"]
    if tr.git_branch:
        meta.append(f"branch `{tr.git_branch}`")
    if tr.version:
        meta.append(f"cc v{tr.version}")
    push("  ".join(meta))
    push(f"session `{tr.session_id[:8]}…`  ·  {tr.raw_lines} events  ·  "
         f"span {_dur(st.span_seconds)}")
    push("")

    # Status line ----------------------------------------------------------
    idle = st.idle_seconds
    idle_str = f"last activity {_dur(idle)} ago" if idle is not None else ""
    push(f"## Status: {_STATUS_LABEL.get(st.status, st.status)}")
    bits = []
    if idle_str:
        bits.append(idle_str)
    if tr.permission_mode:
        bits.append(f"permission-mode `{tr.permission_mode}`")
    if st.pending_tool is not None:
        p = st.pending_tool
        bits.append(f"in-flight: **{p.tool_name}** {_cite(p.line, p.hhmm)}")
    if bits:
        push("· " + "  ·  ".join(bits))
    push("")

    # Safety check (leg ②) — is it safe to keep running? ------------------
    a = assess(st)
    push(f"## Safety: {a.headline}")
    for s in a.signals:
        if s.severity == "info":
            continue
        icon = "🔴" if s.severity == "alarm" else "🟠"
        age = "" if s.recent else " _(earlier)_"
        cites = "  " + " ".join(_cite(n) for n in s.evidence) if s.evidence else ""
        push(f"- {icon} {s.message}{age}{cites}")
    push("")

    # Intent ---------------------------------------------------------------
    if st.intents:
        push("## What it's working on (your asks)")
        for r in st.intents:
            push(f"- {_oneline(r.text, 160)}  {_cite(r.line, r.hhmm)}")
        push("")

    # Plan (only if the session actually used TodoWrite) -------------------
    if st.todos:
        push("## Plan (latest TodoWrite)  " + _cite(st.todos_line))
        mark = {"completed": "✅", "in_progress": "🔄", "pending": "⬜"}
        for t in st.todos:
            push(f"- {mark.get(t['status'], '•')} {_oneline(t['content'], 120)}")
        push("")

    # What it did ----------------------------------------------------------
    push("## What it did")
    if st.tool_counts:
        counts = ", ".join(f"{n}×{c}" for n, c in
                           sorted(st.tool_counts.items(), key=lambda kv: -kv[1]))
        push(f"- tools: {counts}")
    else:
        push("- (no tool activity)")

    changed = st.changed_files
    if changed:
        push(f"- **changed {len(changed)} file(s):**")
        for fc in changed[:max_files]:
            kind = []
            if fc.edits:
                kind.append(f"{fc.edits} edit{'s' if fc.edits != 1 else ''}")
            if fc.writes:
                kind.append(f"{fc.writes} write{'s' if fc.writes != 1 else ''}")
            push(f"    - `{fc.path}` ({', '.join(kind)})  {_cite(fc.last_line, fc.last_hhmm)}")
        if len(changed) > max_files:
            push(f"    - …and {len(changed) - max_files} more")
    push("")

    # Notable commands -----------------------------------------------------
    cmds = st.commands
    if cmds:
        hdr = "## Commands"
        if len(cmds) > max_cmds:
            hdr += f"  (showing last {max_cmds} of {len(cmds)})"
        push(hdr)
        recent = cmds[-max_cmds:]
        icon = {"ok": "✓", "fail": "✗", "unknown": "·"}
        for c in recent:
            tail = f"  {_cite(c.line, c.hhmm)}"
            push(f"- {icon.get(c.status, '·')} `{_oneline(c.cmd, 120)}`{tail}")
        nfail = len(st.failed_commands)
        if nfail:
            push(f"- ⚠ {nfail} command(s) failed in this session")
        push("")

    # Friction / where it might be stuck or off-track ----------------------
    if st.failures:
        push(f"## ⚠ Friction — {len(st.failures)} error result(s)")
        for f in st.failures[-5:]:
            push(f"- **{f.tool}** failed: {_oneline(f.summary, 160)}  "
                 f"{_cite(f.line, f.hhmm)}" +
                 (f" (call {_cite(f.call_line)})" if f.call_line else ""))
        push("")

    # Agent's own last words (faithful: literally what it wrote) -----------
    if st.last_agent_texts:
        push("## Agent's last words")
        for r in st.last_agent_texts:
            push(f"> {_oneline(r.text, 240)}  {_cite(r.line, r.hhmm)}")
        push("")

    push("---")
    push("_Every `[L…]` is a JSONL line in the session transcript — "
         "`sed -n 'Np' <session>.jsonl` to verify. cc-copilot states nothing it can't cite._")
    return "\n".join(L)


def render_check(st: State) -> str:
    """A focused 'can I leave it running?' report (leg ②)."""
    a = assess(st)
    tr = st.tr
    L = [f"# 🛰  cc-copilot check — {_oneline(tr.title or tr.cwd or '?', 60)}"]
    idle = st.idle_seconds
    L.append(f"session `{tr.session_id[:8]}…`  ·  status `{st.status}`"
             + (f"  ·  last activity {_dur(idle)} ago" if idle is not None else ""))
    L.append("")
    L.append(f"## {a.headline}")
    if a.signals:
        for s in a.signals:
            icon = {"alarm": "🔴", "warn": "🟠", "info": "·"}.get(s.severity, "·")
            age = "" if s.recent else " _(earlier)_"
            cites = "  " + " ".join(_cite(n) for n in s.evidence) if s.evidence else ""
            L.append(f"- {icon} {s.message}{age}{cites}")
    else:
        L.append("- (no friction signals)")
    L.append("")
    L.append("_Heuristic, evidence-cited: cc-copilot flags friction; you make the call._")
    return "\n".join(L)


def _oneline(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"
