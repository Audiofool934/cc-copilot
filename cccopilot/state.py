"""Fold a parsed transcript into a deterministic working-state model.

Everything here is computed by rule from the records — no inference, no LLM.
Each derived fact keeps ``evidence`` (the transcript line numbers it came
from) so a brief can cite its sources and a human can re-verify.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import OrderedDict
from typing import Optional

from .transcript import Transcript, Record, MUTATING_TOOLS

# A session whose tail is a tool_call/tool_result was mid-turn. If the last
# event is older than this, it didn't pause to think — it stopped mid-action
# (crashed, was killed, or is genuinely stuck), which is what a returning human
# most needs flagged.
STUCK_SECONDS = 180
_CACHE_MAX = 128
_STATE_CACHE = OrderedDict()


def _short(s: str, n: int = 200) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class FileChange:
    path: str
    edits: int = 0
    writes: int = 0
    last_line: int = 0
    last_hhmm: str = "--:--"

    @property
    def total(self) -> int:
        return self.edits + self.writes


@dataclass
class Command:
    line: int
    hhmm: str
    cmd: str
    desc: str
    status: str = "unknown"   # ok | fail | unknown
    result_line: int = 0


@dataclass
class Failure:
    line: int            # the tool_result line
    hhmm: str
    tool: str            # the tool that failed (resolved via tool_use_id)
    call_line: int       # the originating tool_call line
    summary: str
    target: str = ""     # file_path (Edit/Write) or command (Bash) of the call


@dataclass
class State:
    tr: Transcript
    intents: list = field(default_factory=list)        # [(Record)] recent human prompts
    first_intent: Optional[Record] = None              # the originating goal (first real ask)
    todos: list = field(default_factory=list)          # [{content,status}] if TodoWrite used
    todos_line: int = 0
    files: dict = field(default_factory=dict)          # path -> FileChange
    commands: list = field(default_factory=list)       # [Command]
    failures: list = field(default_factory=list)       # [Failure]
    tool_counts: dict = field(default_factory=dict)    # name -> int
    last_agent_texts: list = field(default_factory=list)  # [Record]
    last_record: Optional[Record] = None
    pending_tool: Optional[Record] = None              # tool_call with no result (mid-run)

    # ---- derived status -------------------------------------------------
    @property
    def status(self) -> str:
        """Classify by the TAIL of the transcript — the single most important
        (and previously most wrong) field for a returning human.

        - tail is human            -> awaiting-agent (your message is unanswered)
        - tail is agent_text       -> idle (agent gave a closing message; your move)
        - tail is tool_call/result -> the agent was MID-TURN (it had not produced
          a closing message). ``running`` if recent, ``stalled`` if it has been
          quiet past ``STUCK_SECONDS`` (interrupted / stuck).
        """
        lr = self.last_record
        if lr is None:
            return "empty"
        if lr.kind == "human":
            return "awaiting-agent"
        if lr.kind == "agent_text":
            return "idle"
        if lr.kind == "system":
            # a captured Codex control event (turn aborted/errored) is terminal:
            # the turn ended, it's the human's move — never "running"/"stalled".
            return "idle"
        idle = self.idle_seconds
        if idle is not None and idle > STUCK_SECONDS:
            return "stalled"
        return "running"

    @property
    def idle_seconds(self) -> Optional[float]:
        lt = self.tr.last_ts
        if lt is None:
            return None
        now = datetime.now(timezone.utc).astimezone()
        return max(0.0, (now - lt).total_seconds())

    @property
    def duration_seconds(self) -> Optional[float]:
        first, last = self.tr.first_ts, self.tr.last_ts
        if first is None or last is None:
            return None
        return max(0.0, (last - first).total_seconds())

    @property
    def span_seconds(self) -> Optional[float]:
        """Wall-clock span across *all* lines (incl. metadata), so even a
        no-activity session reports a real span instead of '?'."""
        first, last = self.tr.first_seen_ts, self.tr.last_seen_ts
        if first is None or last is None:
            return self.duration_seconds
        return max(0.0, (last - first).total_seconds())

    @property
    def changed_files(self) -> list:
        return sorted(self.files.values(), key=lambda c: (-c.last_line))

    @property
    def failed_commands(self) -> list:
        return [c for c in self.commands if c.status == "fail"]


def build(tr: Transcript, intent_window: int = 3, agent_tail: int = 3) -> State:
    st = State(tr=tr)

    # Pass 1: index tool_results by tool_use_id so we can resolve each call's
    # outcome and attribute failures to the tool that caused them.
    result_by_id: dict = {}
    call_by_id: dict = {}
    for r in tr.records:
        if r.kind == "tool_result" and r.tool_id:
            result_by_id[r.tool_id] = r
        elif r.kind == "tool_call" and r.tool_id:
            call_by_id[r.tool_id] = r

    # Pass 2: fold.
    humans: list = []
    todos_seen: Optional[Record] = None
    for r in tr.records:
        if r.kind == "human" and not r.housekeeping:
            humans.append(r)

        elif r.kind == "agent_text" and r.text.strip():
            st.last_agent_texts.append(r)

        elif r.kind == "tool_call":
            name = r.tool_name or "?"
            st.tool_counts[name] = st.tool_counts.get(name, 0) + 1

            if name == "TodoWrite":
                todos_seen = r

            elif name in MUTATING_TOOLS:
                # Only count a mutation that actually landed. A failed Edit
                # ("File does not exist", "File has not been read") returns an
                # is_error result and changed nothing — crediting it would lie
                # to a returning human about the repo state.
                res = result_by_id.get(r.tool_id)
                if res is not None and res.is_error:
                    continue
                path = _input_path(r.tool_input)
                if path:
                    fc = st.files.get(path) or FileChange(path=path)
                    if name == "Write":
                        fc.writes += 1
                    else:
                        fc.edits += 1
                    fc.last_line = r.line
                    fc.last_hhmm = r.hhmm
                    st.files[path] = fc

            elif name == "Bash":
                res = result_by_id.get(r.tool_id)
                status = "unknown"
                rline = 0
                if res is not None:
                    status = "fail" if res.is_error else "ok"
                    rline = res.line
                st.commands.append(Command(
                    line=r.line, hhmm=r.hhmm,
                    cmd=_short(str(r.tool_input.get("command", "")), 240),
                    desc=_short(str(r.tool_input.get("description", "")), 80),
                    status=status, result_line=rline,
                ))

        elif r.kind == "tool_result" and r.is_error:
            call = call_by_id.get(r.tool_id)
            target = ""
            if call:
                if call.tool_name in MUTATING_TOOLS:
                    target = _input_path(call.tool_input)
                elif call.tool_name == "Bash":
                    target = _short(str(call.tool_input.get("command", "")), 120)
            st.failures.append(Failure(
                line=r.line, hhmm=r.hhmm,
                tool=(call.tool_name if call else "?"),
                call_line=(call.line if call else 0),
                summary=_short(r.text, 220),
                target=target,
            ))

    # Most-recent *distinct* human intents (a slash-command re-run or a nudge
    # repeated verbatim shouldn't fill the brief with duplicates).
    distinct: list = []
    seen_norm: set = set()
    for r in reversed(humans):
        key = " ".join(r.text.split())[:120].lower()
        if key in seen_norm:
            continue
        seen_norm.add(key)
        distinct.append(r)
        if len(distinct) >= intent_window:
            break
    st.intents = list(reversed(distinct))
    # the ORIGINATING goal (first real ask) — distinct from the rolling last-3,
    # so a drift check can compare recent work against where the session started.
    st.first_intent = next((r for r in humans if r.text.strip()), None)

    # Latest TodoWrite snapshot, if the session used it at all.
    if todos_seen is not None:
        raw = todos_seen.tool_input.get("todos")
        if isinstance(raw, list):
            st.todos = [
                {"content": t.get("content", ""), "status": t.get("status", "")}
                for t in raw if isinstance(t, dict)
            ]
            st.todos_line = todos_seen.line

    # Keep only the tail of agent narration for the brief.
    st.last_agent_texts = st.last_agent_texts[-agent_tail:]

    # Status anchors: last meaningful record, and any unresolved tool_call.
    # Housekeeping commands (/compact, /clear, …) are transparent here — a
    # trailing /compact must not make a finished session look like it owes you
    # nothing… or owes the agent a reply. Status reflects the real exchange.
    meaningful = [r for r in tr.records
                  if r.kind in ("agent_text", "tool_call", "tool_result")
                  or (r.kind == "human" and not r.housekeeping)
                  or (r.kind == "system"
                      and r.level in ("codex_turn_aborted", "codex_error"))]
    st.last_record = meaningful[-1] if meaningful else None

    # A tool_call whose id never got a result == agent was mid-execution
    # (or the transcript is being written right now). Find the latest such.
    for r in reversed(tr.records):
        if r.kind == "tool_call" and r.tool_id and r.tool_id not in result_by_id:
            st.pending_tool = r
            break
        if r.kind in ("tool_result", "human", "agent_text"):
            break
        if r.kind == "system" and r.level in ("codex_turn_aborted", "codex_error"):
            break  # the turn aborted — the prior tool_call is no longer pending

    return st


def cached_build(path: str, parse_fn, intent_window: int = 3,
                 agent_tail: int = 3) -> State:
    """Build a :class:`State` for a transcript path with stat-based reuse.

    Multi-session/project views often inspect the same sibling transcripts on
    every poll. Parsing is deterministic, so path + size + mtime_ns is a strong
    invalidation key and avoids repeatedly folding unchanged JSONL files.
    """
    p = os.path.abspath(path or "")
    try:
        stt = os.stat(p)
    except OSError:
        # Preserve the pre-cache semantics for callers/tests that provide a
        # virtual parse_fn. Real missing files still fail when parse_fn reads.
        tr = parse_fn(p)
        return (build(tr) if (intent_window, agent_tail) == (3, 3)
                else build(tr, intent_window=intent_window, agent_tail=agent_tail))
    sig = (stt.st_size, getattr(stt, "st_mtime_ns", int(stt.st_mtime * 1e9)))
    hit = _STATE_CACHE.get(p)
    if hit is not None and hit[0] == sig:
        _STATE_CACHE.move_to_end(p)
        return hit[1]
    tr = parse_fn(p)
    state = (build(tr) if (intent_window, agent_tail) == (3, 3)
             else build(tr, intent_window=intent_window, agent_tail=agent_tail))
    _STATE_CACHE[p] = (sig, state)
    _STATE_CACHE.move_to_end(p)
    while len(_STATE_CACHE) > _CACHE_MAX:
        _STATE_CACHE.popitem(last=False)
    return state


def clear_cache() -> None:
    """Test/diagnostic hook for the transcript state cache."""
    _STATE_CACHE.clear()


def _input_path(inp: dict) -> str:
    if not isinstance(inp, dict):
        return ""
    return inp.get("file_path") or inp.get("notebook_path") or ""


@dataclass
class Diff:
    """What changed between two snapshots of a session — for `/diff` and the
    live chat's stall/off-track alerts. Pure data, computed from two States."""
    new_events: int
    status_from: str
    status_to: str
    verdict_from: str
    verdict_to: str
    new_failures: list   # Failure objects present in `new` but not `old`
    new_changed: list    # FileChange objects newly changed (or with more edits)


def diff(old: "State", new: "State") -> Diff:
    from .assess import assess  # local import: assess imports state (avoid cycle)
    v_new = assess(new).verdict
    if old is None:
        return Diff(new.tr.raw_lines, "", new.status, "", v_new,
                    list(new.failures), list(new.changed_files))
    old_fail_lines = {f.line for f in old.failures}
    new_fail = [f for f in new.failures if f.line not in old_fail_lines]
    old_tot = {p: fc.total for p, fc in old.files.items()}
    new_chg = [fc for p, fc in new.files.items() if old_tot.get(p, 0) != fc.total]
    new_chg.sort(key=lambda c: -c.last_line)
    return Diff(
        new_events=max(0, new.tr.raw_lines - old.tr.raw_lines),
        status_from=old.status, status_to=new.status,
        verdict_from=assess(old).verdict, verdict_to=v_new,
        new_failures=new_fail, new_changed=new_chg,
    )
