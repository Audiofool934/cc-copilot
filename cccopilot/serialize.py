"""JSON serialization of the data model for wire transport (the server) and the
CLI ``state --json`` command.

This is the first cut of the structured render model: typed JSON the GUI will
consume to render timelines, diffs, and boards natively (stage 5 expands it).
It lives in one place so the CLI and the server never drift apart in what they
expose about a State, Transcript, or SessionRef.
"""

from __future__ import annotations

from dataclasses import asdict

from . import assess as A
from .state import State
from .transcript import Record, Transcript


def record_to_dict(r: Record) -> dict:
    """One normalized transcript record as JSON."""
    return {
        "line": r.line,
        "kind": r.kind,
        "ts": r.ts.isoformat() if r.ts else None,
        "hhmm": r.hhmm,
        "raw_ts": r.raw_ts,
        "text": r.text,
        "tool_id": r.tool_id,
        "tool_name": r.tool_name,
        "tool_input": r.tool_input,
        "is_error": r.is_error,
        "level": r.level,
        "housekeeping": r.housekeeping,
    }


def transcript_to_dict(tr: Transcript) -> dict:
    """A Transcript as JSON: metadata plus the normalized record list."""
    return {
        "path": tr.path,
        "session_id": tr.session_id,
        "cwd": tr.cwd,
        "git_branch": tr.git_branch,
        "version": tr.version,
        "permission_mode": tr.permission_mode,
        "title": tr.title,
        "title_is_custom": tr.title_is_custom,
        "raw_lines": tr.raw_lines,
        "parse_errors": tr.parse_errors,
        "first_seen_ts": tr.first_seen_ts.isoformat() if tr.first_seen_ts else None,
        "last_seen_ts": tr.last_seen_ts.isoformat() if tr.last_seen_ts else None,
        "token_usage": tr.token_usage,
        "records": [record_to_dict(r) for r in tr.records],
    }


def session_ref_to_dict(r) -> dict:
    """A SessionRef as JSON (the discovery list the GUI picks from)."""
    return {
        "path": r.path,
        "session_id": r.session_id,
        "mtime": r.mtime,
        "size": r.size,
        "title": r.title,
        "own": r.own,
        "agent": r.agent,
        "model": r.model,
        "live": r.live,
        "nickname": r.nickname,
        "forked_from": r.forked_from,
        "hhmm": r.hhmm,
    }


def state_to_dict(st: State) -> dict:
    """A State as JSON - the same shape the CLI ``state --json`` emits."""
    a = A.assess(st)
    tr = st.tr
    return {
        "assessment": {
            "verdict": a.verdict,
            "headline": a.headline,
            "signals": [
                {"kind": s.kind, "severity": s.severity,
                 "message": s.message, "evidence": s.evidence}
                for s in a.signals
            ],
        },
        "session_id": tr.session_id,
        "cwd": tr.cwd,
        "git_branch": tr.git_branch,
        "version": tr.version,
        "permission_mode": tr.permission_mode,
        "events": tr.raw_lines,
        "status": st.status,
        "idle_seconds": st.idle_seconds,
        "duration_seconds": st.duration_seconds,
        "tool_counts": st.tool_counts,
        "intents": [{"line": r.line, "ts": r.raw_ts, "text": r.text} for r in st.intents],
        "todos": st.todos,
        "changed_files": [asdict(c) for c in st.changed_files],
        "commands": [asdict(c) for c in st.commands],
        "failures": [asdict(f) for f in st.failures],
        "pending_tool": (
            {"line": st.pending_tool.line, "tool": st.pending_tool.tool_name}
            if st.pending_tool else None
        ),
    }


def diff_to_dict(d) -> dict:
    """A ``State.Diff`` (what changed between two snapshots) as JSON - for
    ``/diff`` and the watch/alert transition detection."""
    return {
        "new_events": d.new_events,
        "status_from": d.status_from,
        "status_to": d.status_to,
        "verdict_from": d.verdict_from,
        "verdict_to": d.verdict_to,
        "new_failures": [asdict(f) for f in d.new_failures],
        "new_changed": [asdict(fc) for fc in d.new_changed],
    }


def since_view_to_dict(view) -> dict:
    """A ``since.SinceView`` as JSON: the structured delta behind the rendered
    ``text`` - new turns, messages, commands, failures, changed files, the
    status/verdict transition, and the pending-ask cue. The GUI ``/diff`` view
    renders this natively instead of the Markdown."""
    return {
        "cutoff_line": view.cutoff_line,
        "label": view.label,
        "new_events": view.new_events,
        "nothing_new": view.nothing_new,
        "pending_ask": view.pending_ask,
        "new_humans": [record_to_dict(r) for r in view.new_humans],
        "new_agent": [record_to_dict(r) for r in view.new_agent],
        "new_commands": [asdict(c) for c in view.new_commands],
        "new_failures": [asdict(f) for f in view.new_failures],
        "new_changed_files": [asdict(fc) for fc in view.new_changed_files],
        "diff": diff_to_dict(view.diff) if view.diff is not None else None,
        "text": view.text,
    }