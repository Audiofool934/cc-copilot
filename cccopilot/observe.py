"""Deterministic agent-observability report.

The regular brief answers "what happened?" This module answers the cockpit
question: "where should my attention go right now?" It stays fully read-only
and evidence-cited, using only the existing transcript/project scope model.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from . import assess as A, locate as LOC, scope as SC, state as S, sources as SRC
from .brief import _dur, _oneline


@dataclass
class ObservationItem:
    ref: Any
    st: S.State
    assessment: A.Assessment
    session_id: str
    title: str


@dataclass
class ObservationReport:
    scope: str
    root: str
    total: int
    selected: int
    items: List[ObservationItem]


def build(path: str, st: Optional[S.State] = None, scope: str = SC.SESSION,
          sessions=None) -> ObservationReport:
    """Build a ranked, deterministic attention model for a scope."""
    sc = SC.normalize(scope)
    if st is None and path and os.path.isfile(path):
        st = S.build(SRC.parse(path))
    root = _project_root(path, st)

    if sc == SC.SESSION:
        items = [_item(_ref_for(path, st), st)] if st is not None else []
        return ObservationReport(sc, root, len(items), len(items), items)

    selectors = SC.parse_selectors(sessions)
    all_refs = SC.resolve_session_refs(path, [])
    refs = SC.resolve_session_refs(path, selectors)
    here = os.path.abspath(path) if path else ""
    items = []
    for ref in refs:
        try:
            cur = (st if st is not None and os.path.abspath(ref.path) == here
                   else S.build(SRC.parse(ref.path)))
            items.append(_item(ref, cur))
        except Exception:
            continue
    items.sort(key=_item_rank)
    return ObservationReport(sc, root, len(all_refs), len(items), items)


def render(path: str, st: Optional[S.State] = None, scope: str = SC.SESSION,
           sessions=None, max_sessions: int = 8) -> str:
    """Render the v0.5 observer report."""
    report = build(path, st, scope, sessions)
    qualified = report.scope != SC.SESSION
    title = _oneline(report.root, 78)
    L = [f"# cc-copilot observe - {title}",
         f"scope `{report.scope}` · source: {_source(report, sessions)}",
         ""]

    if not report.items:
        L.extend([
            "## Now",
            "- (no live work-session evidence in this scope)",
            "",
            "## Next Human Decision",
            "- Attach to a live session or narrow the scope to sessions that still exist.",
        ])
        return "\n".join(L)

    L.append("## Now")
    for item in report.items[:max_sessions]:
        L.append(_board_line(item, qualified))
        sig = _primary_signal(item)
        if sig is not None:
            L.append(f"  - {sig.severity}: {_oneline(sig.message, 120)}"
                     f"{_cites(item, sig.evidence, qualified)}")
        for info in _info_signals(item):
            L.append(f"  - info: {_oneline(info.message, 120)}"
                     f"{_cites(item, info.evidence, qualified)}")
    if report.selected > max_sessions:
        L.append(f"- ...and {report.selected - max_sessions} more session(s)")
    L.append("")

    L.append("## Attention Queue")
    attention = [i for i in report.items if _needs_attention(i)]
    if not attention:
        L.append("- clear: nothing currently needs human attention; keep monitoring.")
    else:
        for item in attention[:max_sessions]:
            level, text = _decision(item, qualified)
            L.append(f"- {level}: {text}")
    L.append("")

    L.append("## Next Human Decision")
    _level, decision = _decision(report.items[0], qualified)
    L.append(f"- {decision}")
    L.append("")

    L.append("## Recent Evidence")
    for line in _recent_evidence(report.items, qualified):
        L.append(f"- {line}")
    L.append("")
    L.extend(_project_glance(report.root))

    L.append("")
    L.append("_Observer reports are deterministic and read-only. Transcript citations "
             "point to JSONL lines; project citations come from local git reads._")
    return "\n".join(L)


def timeline_lines(path: str, st: Optional[S.State] = None,
                   scope: str = SC.SESSION, sessions=None, limit: int = 2
                   ) -> List[Tuple[str, str]]:
    """Short observer lines for the TUI activity strip.

    Returns ``[(level, text)]`` where level is alarm/warn/info/clear.
    """
    try:
        report = build(path, st, scope, sessions)
    except ValueError as e:
        return [("warn", "scope error: " + str(e))]
    except OSError:
        return [("warn", "attention: transcript unavailable")]
    if not report.items:
        return [("warn", "attention: no live session evidence")]
    qualified = report.scope != SC.SESSION
    out = []
    for item in [i for i in report.items if _needs_attention(i)][:limit]:
        level, text = _decision(item, qualified)
        out.append((_line_level(level), text))
    if not out:
        _level, text = _decision(report.items[0], qualified)
        out.append(("clear", text))
    return out


def next_step(path: str, st: Optional[S.State] = None, scope: str = SC.SESSION,
              sessions=None) -> str:
    """Deterministic 'what should I do next' — the LLM-free fallback for `/now`.

    Reuses the observer's ranked attention model: the top-ranked session drives
    the primary recommendation; any other session that still needs attention is
    appended so a fleet glance never silently buries a stalled sibling."""
    report = build(path, st, scope, sessions)
    if not report.items:
        return "→ no live session evidence in this scope — attach a live session or narrow the scope."
    qualified = report.scope != SC.SESSION
    _level, decision = _decision(report.items[0], qualified)
    lines = [f"→ {decision}"]
    for item in report.items[1:]:
        if _needs_attention(item):
            _lvl, text = _decision(item, qualified)
            lines.append(f"  also: {text}")
        if len(lines) >= 4:
            break
    return "\n".join(lines)


def _item(ref, st: S.State) -> ObservationItem:
    sid = (getattr(st.tr, "session_id", "") or getattr(ref, "session_id", "")
           or os.path.basename(getattr(ref, "path", ""))[:-6])
    title = (getattr(st.tr, "title", "") or getattr(ref, "title", "")
             or (st.intents[-1].text if st.intents else "") or "(untitled)")
    return ObservationItem(ref=ref, st=st, assessment=A.assess(st),
                           session_id=sid[:8] or "session", title=title)


def _ref_for(path: str, st: Optional[S.State]):
    sid = ""
    title = ""
    if st is not None:
        sid = getattr(st.tr, "session_id", "") or ""
        title = getattr(st.tr, "title", "") or ""
    if not sid and path:
        sid = os.path.basename(path)[:-6]
    try:
        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)
    except OSError:
        mtime = 0
        size = 0
    return LOC.SessionRef(path or "", sid or "session", mtime, size, title, False)


def _item_rank(item: ObservationItem):
    st, verdict = item.st, item.assessment.verdict
    if st.status == "stalled" or verdict == "intervene":
        rank = 0
    elif verdict == "review":
        rank = 1
    elif st.status == "awaiting-agent":
        rank = 2
    elif st.status == "running":
        rank = 3
    elif st.status == "idle":
        rank = 4
    else:
        rank = 5
    idle = st.idle_seconds if st.idle_seconds is not None else 9e9
    return (rank, idle, -getattr(item.ref, "mtime", 0))


def _needs_attention(item: ObservationItem) -> bool:
    return (item.st.status == "stalled"
            or item.assessment.verdict in ("intervene", "review")
            or item.st.status == "awaiting-agent")


def _primary_signal(item: ObservationItem):
    for sig in item.assessment.signals:
        if sig.severity in ("alarm", "warn"):
            return sig
    return None


def _info_signals(item: ObservationItem):
    """INFO-severity heads-ups (e.g. goal drift). Shown under a session in the
    Now view but deliberately NOT in the attention queue — they inform, they
    don't escalate."""
    return [s for s in item.assessment.signals if s.severity == "info"]


def _decision(item: ObservationItem, qualified: bool) -> Tuple[str, str]:
    sig = _primary_signal(item)
    cite_lines = sig.evidence if sig is not None else []
    if item.assessment.verdict == "intervene" or item.st.status == "stalled":
        msg = sig.message if sig is not None else "session stopped mid-turn"
        return "INTERVENE", (f"Intervene in `{item.session_id}` now: {_oneline(msg, 120)}"
                             f"{_cites(item, cite_lines, qualified)}")
    if item.assessment.verdict == "review":
        msg = sig.message if sig is not None else "friction detected"
        return "REVIEW", (f"Review `{item.session_id}` before continuing: {_oneline(msg, 120)}"
                          f"{_cites(item, cite_lines, qualified)}")
    if item.st.status == "awaiting-agent":
        r = item.st.last_record
        return "WAIT", (f"`{item.session_id}` has not answered the latest human turn yet; "
                        f"wait or check whether the agent launched{_cite(item, r.line if r else 0, qualified)}")
    if item.st.status == "running":
        r = item.st.last_record
        return "MONITOR", (f"Let `{item.session_id}` continue; no friction is visible yet"
                           f"{_cite(item, r.line if r else 0, qualified)}")
    if item.st.status == "idle":
        r = item.st.last_record
        return "READY", (f"Read `{item.session_id}`'s closing message and decide the next instruction"
                         f"{_cite(item, r.line if r else 0, qualified)}")
    return "EMPTY", f"`{item.session_id}` has no substantive activity yet."


def _line_level(level: str) -> str:
    return {"INTERVENE": "alarm", "REVIEW": "warn", "WAIT": "info",
            "MONITOR": "clear", "READY": "clear", "EMPTY": "info"}.get(level, "info")


def _board_line(item: ObservationItem, qualified: bool) -> str:
    st, a = item.st, item.assessment
    last = st.last_record
    tail = _record_summary(last)
    return (f"- `{item.session_id}` {st.status.upper()} · safety `{a.verdict}` · "
            f"idle {_dur(st.idle_seconds)} · {st.tr.raw_lines} ev · "
            f"{_oneline(item.title, 72)}"
            + (f" · {tail}" if tail else "")
            + _cite(item, last.line if last else 0, qualified))


def _record_summary(record) -> str:
    if record is None:
        return ""
    if record.kind == "human":
        return "latest human turn"
    if record.kind == "agent_text":
        return "latest agent message"
    if record.kind == "agent_thinking":
        return "agent thinking"
    if record.kind == "tool_call":
        target = ""
        inp = record.tool_input if isinstance(record.tool_input, dict) else {}
        if record.tool_name == "Bash":
            target = inp.get("description") or inp.get("command") or ""
        else:
            target = inp.get("file_path") or inp.get("notebook_path") or ""
        return _oneline(f"latest {record.tool_name or 'tool'} {target}".strip(), 80)
    if record.kind == "tool_result":
        return "latest tool error" if record.is_error else "latest tool result"
    return record.kind


def _recent_evidence(items: List[ObservationItem], qualified: bool) -> List[str]:
    rows = []
    failures = []
    changes = []
    last_records = []
    for item in items:
        # Across sessions the JSONL line number is meaningless (a long stale
        # session outranks a short fresh one), so order by wall-clock time and
        # only tiebreak on line within a session.
        ts_by_line = {r.line: (r.ts.timestamp() if r.ts else 0.0)
                      for r in item.st.tr.records}
        for f in item.st.failures[-2:]:
            failures.append((ts_by_line.get(f.line, 0.0), f.line, item, f))
        for fc in item.st.changed_files[:3]:
            changes.append((ts_by_line.get(fc.last_line, 0.0), fc.last_line, item, fc))
        if item.st.last_record is not None:
            lr = item.st.last_record
            last_records.append((ts_by_line.get(lr.line, 0.0), lr.line, item, lr))
    failures.sort(reverse=True, key=lambda x: (x[0], x[1]))
    changes.sort(reverse=True, key=lambda x: (x[0], x[1]))
    last_records.sort(reverse=True, key=lambda x: (x[0], x[1]))

    for _ts, _line, item, f in failures[:4]:
        rows.append(f"`{item.session_id}` {f.tool} failed: {_oneline(f.summary, 110)}"
                    f"{_cite(item, f.line, qualified)}")
    for _ts, _line, item, fc in changes[:6]:
        kinds = []
        if fc.edits:
            kinds.append(f"{fc.edits} edit{'s' if fc.edits != 1 else ''}")
        if fc.writes:
            kinds.append(f"{fc.writes} write{'s' if fc.writes != 1 else ''}")
        rows.append(f"`{item.session_id}` changed `{fc.path}` ({', '.join(kinds)})"
                    f"{_cite(item, fc.last_line, qualified)}")
    for _ts, _line, item, r in last_records[:3]:
        rows.append(f"`{item.session_id}` tail: {_record_summary(r)}"
                    f"{_cite(item, r.line, qualified)}")
    return rows or ["(no recent evidence beyond the status board)"]


def _source(report: ObservationReport, sessions) -> str:
    if report.scope == SC.SESSION:
        return f"{report.selected} transcript"
    selected = f"{report.selected} selected of {report.total}" if SC.parse_selectors(sessions) \
        else f"{report.selected} of {report.total}"
    return selected + " work-session transcript(s)"


def _project_root(path: str, st: Optional[S.State]) -> str:
    tr = getattr(st, "tr", None)
    cwd = (getattr(tr, "cwd", "") if tr is not None else "") or SRC.read_cwd(path or "")
    return os.path.abspath(cwd or os.getcwd())


def _cite(item: ObservationItem, line: int, qualified: bool) -> str:
    if not line:
        return ""
    return f"  [{item.session_id}:L{line}]" if qualified else f"  [L{line}]"


def _cites(item: ObservationItem, lines: List[int], qualified: bool) -> str:
    if not lines:
        return ""
    return "".join(_cite(item, n, qualified) for n in lines)


def _project_glance(root: str) -> List[str]:
    L = ["## Project Glance"]
    top = _git(root, "rev-parse", "--show-toplevel")
    branch = _git(root, "branch", "--show-current") or _git(root, "rev-parse", "--short", "HEAD")
    status = _git(root, "status", "--short")
    if top:
        L.append(f"- repository root `{top.splitlines()[0]}`  [git:root]")
    if branch:
        L.append(f"- branch `{branch.splitlines()[0]}`  [git:branch]")
    if status:
        rows = status.splitlines()
        L.append(f"- working tree has {len(rows)} changed path(s)  [git:status]")
        for row in rows[:8]:
            L.append(f"  - `{row}`  [git:status]")
        if len(rows) > 8:
            L.append(f"  - ...and {len(rows) - 8} more  [git:status]")
    else:
        L.append("- working tree clean or git unavailable  [git:status]")
    return L


def _git(root: str, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""
