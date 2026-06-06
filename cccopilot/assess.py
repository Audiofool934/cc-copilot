"""Leg ② — a deterministic "is it safe to continue / did it go off track?" read.

This is the differentiated layer: not *what* the agent did, but *how it's
going*. Every signal is computed by rule from the :class:`~cccopilot.state.State`
and carries the transcript lines that triggered it — same faithfulness contract
as the brief. No LLM: a friction signal is something you can point at.

The verdict is deliberately hedged. cc-copilot flags **friction**; the human
makes the call. "🟢 clear" means *no friction signals were detected*, not a
guarantee the agent is on track.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List

from .state import State


# severity ordering for rollup
_RANK = {"alarm": 0, "warn": 1, "info": 2}

# how many trailing commands count as "recent" for failure-density checks
RECENT_CMD_WINDOW = 8
# a command run at least this many times is a suspected retry loop
REPEAT_LOOP = 4
# consecutive failed commands at/above this == flailing
FAIL_STREAK_ALARM = 3
# a single file with this many failed mutations == fighting the file
EDIT_THRASH = 2

_TEST_RE = re.compile(r"\b(pytest|jest|vitest|go test|cargo test|npm (run )?test|"
                      r"xcodebuild test|tox|unittest|rspec|mocha|gradle test|mvn test)\b",
                      re.I)


@dataclass
class Signal:
    kind: str
    severity: str           # alarm | warn | info
    message: str
    evidence: List[int] = field(default_factory=list)   # jsonl line numbers
    recent: bool = True      # is its latest evidence near the transcript tail?


@dataclass
class Assessment:
    verdict: str            # clear | review | intervene | idle | awaiting | empty
    headline: str
    signals: List[Signal] = field(default_factory=list)

    @property
    def alarms(self) -> List[Signal]:
        return [s for s in self.signals if s.severity == "alarm"]

    @property
    def warns(self) -> List[Signal]:
        return [s for s in self.signals if s.severity == "warn"]


_VERDICT_HEADLINE = {
    "intervene": "🔴 INTERVENE — looks stuck or off-track; don't let it keep running blind",
    "review":    "🟠 REVIEW — friction detected; check these before continuing",
    "clear":     "🟢 CLEAR — no friction signals (working/answered cleanly)",
    "idle":      "⚪ IDLE — agent finished its turn; nothing running to be unsafe",
    "awaiting":  "🟡 AWAITING YOU — it's blocked on your reply, not on a problem",
    "empty":     "∅ no agent activity to assess",
}


def assess(st: State) -> Assessment:
    signals: List[Signal] = []

    # 1) stalled mid-action — the strongest "is it stuck" tell.
    if st.status == "stalled":
        lr = st.last_record
        idle = st.idle_seconds
        mins = f"{int(idle // 60)}m" if idle else "?"
        signals.append(Signal(
            "stalled", "alarm",
            f"stopped mid-action ~{mins} ago with no closing message — "
            f"interrupted, crashed, or stuck",
            [lr.line] if lr else [],
        ))

    # 2) consecutive failed-command streak — flailing on the shell.
    streak, streak_lines, best, best_lines = 0, [], 0, []
    for c in st.commands:
        if c.status == "fail":
            streak += 1
            streak_lines.append(c.line)
            if streak > best:
                best, best_lines = streak, list(streak_lines)
        else:
            streak, streak_lines = 0, []
    if best >= FAIL_STREAK_ALARM:
        signals.append(Signal(
            "fail_streak", "alarm",
            f"{best} commands failed in a row — likely flailing on the same problem",
            best_lines[-5:],
        ))

    # 3) recent failure density (if not already an obvious streak).
    recent = st.commands[-RECENT_CMD_WINDOW:]
    rfail = [c for c in recent if c.status == "fail"]
    if best < FAIL_STREAK_ALARM and len(rfail) >= 3:
        signals.append(Signal(
            "recent_failures", "warn",
            f"{len(rfail)} of the last {len(recent)} commands failed",
            [c.line for c in rfail][-5:],
        ))

    # 4) fighting a file — repeated failed edits to the same path.
    edit_fails = Counter()
    edit_fail_lines = {}
    for f in st.failures:
        if f.tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and f.target:
            edit_fails[f.target] += 1
            edit_fail_lines.setdefault(f.target, []).append(f.line)
    for path, n in edit_fails.items():
        if n >= EDIT_THRASH:
            signals.append(Signal(
                "edit_thrash", "warn",
                f"{n} failed edits to `{_base(path)}` — edit/read race or "
                f"fighting the file",
                edit_fail_lines[path][-4:],
            ))

    # 5) retry loop — the exact same command run many times.
    cmd_counts = Counter(c.cmd for c in st.commands if c.cmd)
    for cmd, n in cmd_counts.items():
        if n >= REPEAT_LOOP:
            lines = [c.line for c in st.commands if c.cmd == cmd]
            signals.append(Signal(
                "retry_loop", "warn",
                f"ran the same command {n}× — possible retry loop",
                lines[-4:],
            ))

    # 6) failing tests — concrete "it's not green".
    for c in st.commands:
        if c.status == "fail" and _TEST_RE.search(c.cmd or ""):
            signals.append(Signal(
                "test_failing", "warn",
                "a test command failed — suite is not green",
                [c.line],
            ))
            break

    # ---- recency: friction near the tail is actionable now; friction the
    #      agent already recovered from (and then kept working / finished) is
    #      a heads-up, not an emergency. ------------------------------------
    tail = st.last_record.line if st.last_record else st.tr.raw_lines
    window = max(50, st.tr.raw_lines * 8 // 100)
    for s in signals:
        s.recent = (not s.evidence) or (max(s.evidence) >= tail - window)

    # ---- roll up to a verdict --------------------------------------------
    signals.sort(key=lambda s: (_RANK.get(s.severity, 9), 0 if s.recent else 1))
    has_signal = bool(signals)
    # INTERVENE is reserved for "it's running RIGHT NOW and going wrong":
    # an active session (running/stalled) with a live alarm. A finished/idle
    # session can't be intervened on — its friction is REVIEW at most.
    active = st.status in ("running", "stalled")
    live_alarm = st.status == "stalled" or any(
        s.severity == "alarm" and s.recent for s in signals)

    if st.status == "empty":
        verdict = "empty"
    elif active and live_alarm:
        verdict = "intervene"
    elif has_signal:
        verdict = "review"
    elif st.status == "awaiting-agent":
        verdict = "awaiting"
    elif st.status == "idle":
        verdict = "idle"
    else:
        verdict = "clear"

    headline = _VERDICT_HEADLINE[verdict]
    if verdict == "review" and st.status in ("idle", "awaiting-agent"):
        headline += (" (agent is "
                     + ("idle" if st.status == "idle" else "waiting on you")
                     + ", not running)")

    return Assessment(verdict=verdict, headline=headline, signals=signals)


def _base(path: str) -> str:
    return path.rsplit("/", 1)[-1] if path else path
