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
from .transcript import MUTATING_TOOLS


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

_TEST_RE = re.compile(
    r"\b(?:pytest|jest|vitest|go test|cargo test|"
    r"(?:npm|yarn|pnpm|bun)(?: run)? test|make (?:test|check)|"
    r"xcodebuild test|tox|unittest|rspec|mocha|gradle test|mvn test|ctest)\b",
    re.I)

# "says vs does": a closing message claims an outcome the turn's own evidence
# doesn't back. Two high-precision patterns only (the cry-wolf risk is real, so
# this stays warn/REVIEW — it never drives INTERVENE). A) it claims tests/build
# pass; B) it claims a fix landed after editing code — in both cases with no
# successful verification run this turn. Each fires a CITED PAIR (the claim line
# + the evidence that should exist and doesn't); it is evidence to check, never
# an accusation that the agent lied.
# Tests-pass claim — require an explicit success QUANTIFIER (all / every / the
# suite / everything / green), never a bare "tests pass". Bare forms also match
# honest failures ("I couldn't make tests pass", "some tests passed, but one
# failed"), which the backward negation window can't always catch.
_CLAIM_TESTS_PASS = re.compile(
    r"\ball(?: \d+| of)? tests?(?: (?:now|again))? (?:pass|passing|passed|green|succeed|succeeded)\b"
    r"|\bevery test(?: now| again)? (?:passes|passed|is green)\b"
    r"|\b(?:the )?(?:test )?suite (?:is |now )?(?:green|passes|passed|passing)\b"
    r"|\btests? are (?:all )?(?:now )?(?:green|passing)\b"
    r"|\ball tests? green\b"
    r"|\beverything (?:passes|passed|is green)\b"
    r"|\ball green\b", re.I)
# Build-pass claim — checked against build commands, NOT test runners.
_CLAIM_BUILD_PASS = re.compile(
    r"\bbuild (?:passes|passed|succeeds|succeeded|is green|now (?:passes|builds|succeeds))\b"
    r"|\b(?:compiles|builds) (?:clean|cleanly|fine|now)\b"
    r"|\bcompilation (?:passes|succeeds|succeeded)\b", re.I)
# A fix CLAIM, not the adjective "fixed". "fixed/resolved" only counts as a fix
# claim when used as a verb (followed by an object — "fixed it", "fixed the login
# bug") or in a fix-EVENT state ("now fixed", "it's resolved", "has been fixed").
# The bare adjective uses ("fixed-width layout", "fixed position", "the width is
# fixed") are NOT claims and must not trip the high-precision says-vs-does signal.
_CLAIM_FIXED = re.compile(
    r"\b(?:fixed|resolved)\b(?=\s+(?:it|this|that|the|a|an|all|my|our|your|its|"
    r"these|those|everything)\b)"
    r"|\b(?:now|is now|are now|has been|have been|should be|should now be|"
    r"it'?s|that'?s)\s+(?:fixed|resolved)\b"
    r"|\b(?:works|working) now\b|\bnow works\b"
    r"|\bshould (?:now )?work\b"
    r"|\bis now (?:working|passing|green)\b"
    r"|\bthat (?:should (?:do it|fix it|work)|fixes it)\b", re.I)
# A "build passes" claim is verified by a build command, not a test runner — so
# the verification set is tests OR builds (else a green `npm run build` reads as
# "nothing ran" and falsely escalates to REVIEW).
_BUILD_RE = re.compile(
    r"\b(?:npm|yarn|pnpm|bun)(?: run)? build\b|\bcmake --build\b|"
    r"\b(?:cargo build|go build|gradle build|"
    r"mvn (?:package|install|compile)|xcodebuild(?: build)?|tsc|webpack|"
    r"vite build|docker build)\b"
    # bare `make` (or make build/all/…) is a build, but NOT `make test/check`
    # — those are tests, and conflating them breaks the tests-vs-build split.
    r"|\bmake(?:\s+(?:build|all|release|compile|install))?\b(?!\s+(?:test|check)\b)", re.I)
# A negation right before a positive claim flips its meaning — "not all tests
# pass", "the bug is not fixed yet" must NOT read as a success claim.
_NEG_BEFORE = re.compile(
    r"(?i)\b(?:not|no|never|without|isn'?t|aren'?t|wasn'?t|weren'?t|don'?t|"
    r"doesn'?t|didn'?t|can'?t|cannot|couldn'?t|won'?t|wouldn'?t|shouldn'?t|"
    r"haven'?t|hasn'?t|hadn'?t|unable|un(?:fixed|resolved)|"
    r"fail(?:ed|ing|s)?)\b[\s\w]{0,16}$")


def _asserts(text: str, rx) -> bool:
    """True iff ``text`` makes the claim ``rx`` matches *without* a negation
    immediately before it ('all tests pass' → yes; 'not all tests pass' → no)."""
    return any(not _NEG_BEFORE.search(text[:m.start()]) for m in rx.finditer(text))


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

    # 7) says-vs-does — a closing claim the turn's own evidence doesn't back.
    cu = _claim_unverified(st)
    if cu is not None:
        signals.append(cu)

    # 8) a Codex turn that ended abnormally (aborted/interrupted/error) at the tail.
    ta = _turn_ended_abnormally(st)
    if ta is not None:
        signals.append(ta)

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


def _claim_unverified(st: State):
    """Detect a closing success/fix claim the turn's own evidence doesn't back.

    High-precision and REVIEW-only: fires only at a *closing* message (the agent
    handed the turn back, status ``idle``), bounded to the current turn (records
    after the last real human ask), and only on two explicit claim shapes. Cross-
    agent by construction — it reads the normalized :class:`State`, so a Claude
    transcript and a Codex rollout that say the same thing trip it the same way.
    Returns a cited :class:`Signal` or ``None``.
    """
    if st.status != "idle":
        return None
    closing = next((r for r in reversed(st.tr.records)
                    if r.kind == "agent_text" and r.text.strip()), None)
    if closing is None:
        return None
    text = closing.text

    # Bound to the current turn: everything after the last genuine human ask.
    turn_start = 0
    for r in st.tr.records:
        if r.kind == "human" and not r.housekeeping:
            turn_start = r.line

    # The closing message must belong to the CURRENT turn. Otherwise (e.g. a new
    # human ask, then a Codex turn_aborted before any reply) `closing` would be a
    # prior turn's message, and we'd judge a stale "all tests pass" against this
    # turn's (empty) evidence — a bogus warning beside the real abort signal.
    if closing.line <= turn_start:
        return None

    # Only SUCCESSFUL mutations count (a failed Edit/Write changed nothing, same
    # as State.build) — else a failed edit after a passing test would push
    # last_edit past the verification and falsely flag the claim.
    errs = {r.tool_id: r.is_error for r in st.tr.records
            if r.kind == "tool_result" and r.tool_id}
    mutated = [r for r in st.tr.records
               if r.line > turn_start and r.kind == "tool_call"
               and r.tool_name in MUTATING_TOOLS
               and not errs.get(r.tool_id, False)]
    # Verification only "covers" the claim if it ran AFTER the last edit — a test
    # that passed and was then followed by another edit no longer exercises the
    # final code, so it doesn't substantiate a closing "it passes / I fixed it".
    last_edit = max((r.line for r in mutated), default=0)
    turn_cmds = [c for c in st.commands if c.line > turn_start]
    tests = [c for c in turn_cmds if _TEST_RE.search(c.cmd or "")]
    builds = [c for c in turn_cmds if _BUILD_RE.search(c.cmd or "")]
    ok_test = any(c.status == "ok" and c.line > last_edit for c in tests)
    ok_build = any(c.status == "ok" and c.line > last_edit for c in builds)
    covered = any(c.line > last_edit for c in tests + builds)

    # A) tests-pass claim with no PASSING TEST covering the final code (a green
    #    build is not evidence a test claim is true, and vice-versa).
    if _asserts(text, _CLAIM_TESTS_PASS) and not ok_test:
        return Signal(
            "claim_unverified", "warn",
            "closing message claims the tests pass, but no passing test run "
            "covers the latest code this turn — verify before trusting it",
            [closing.line] + [c.line for c in tests][-2:],
        )

    # A2) build-pass claim with no passing BUILD covering the final code.
    if _asserts(text, _CLAIM_BUILD_PASS) and not ok_build:
        return Signal(
            "claim_unverified", "warn",
            "closing message claims the build passes, but no passing build "
            "covers the latest code this turn — verify before trusting it",
            [closing.line] + [c.line for c in builds][-2:],
        )

    # B) a fix/correctness claim after editing code, with nothing run AFTER the
    #    last edit to verify it (a test OR a build both count as verification).
    if _asserts(text, _CLAIM_FIXED) and mutated and not covered:
        paths = {r.tool_input.get("file_path") or r.tool_input.get("path")
                 for r in mutated}
        n = len(paths - {None}) or len(mutated)
        return Signal(
            "claim_unverified", "warn",
            f"claims a fix after editing {n} file(s) but nothing was run to "
            f"verify the latest code this turn — verify before trusting it",
            [closing.line] + [r.line for r in mutated][-2:],
        )
    return None


def _turn_ended_abnormally(st: State):
    """A Codex turn that ended abnormally — aborted/interrupted, or a surfaced
    error — captured (only) from the ``event_msg`` control stream as ``system``
    records. Fires (warn) only when such an event is the CURRENT tail record (the
    last meaningful thing that happened), so a returning human is oriented ("your
    last turn didn't finish") and a later completed turn naturally suppresses it.
    Codex-specific by its level strings — Claude transcripts never carry them, so
    this never affects the Claude path. Returns a cited :class:`Signal` or None."""
    lr = st.last_record
    if lr is None or lr.kind != "system":
        return None
    if lr.level == "codex_error":
        return Signal("turn_error", "warn",
                      f"the agent surfaced an error: {lr.text}", [lr.line])
    if lr.level == "codex_turn_aborted":
        return Signal("turn_aborted", "warn",
                      f"the agent's last turn ended early ({lr.text}) — it did "
                      f"not finish", [lr.line])
    return None


def _base(path: str) -> str:
    return path.rsplit("/", 1)[-1] if path else path
