"""Leg ③ — the grounded narration / observer-chat layer.

The whole point of cc-copilot is *not* being a second hallucinating agent. So
the LLM here never sees raw transcripts or ambient repo access — it sees a
**deterministic, evidence-cited context** produced by legs ①/② (and, for
project scope, read-only file facts), keeping citations for observed facts.
It can synthesize and recommend from that evidence; it doesn't invent.

The backend is pluggable (see :mod:`cccopilot.backends`): a local agent CLI
(`claude`, `codex`, `gemini`, `llm`) or any OpenAI-compatible HTTP API
(`deepseek`, `openai`, `openrouter`, `ollama`, …). Default is `codex`. Pick one
with ``backend=...`` / ``--backend`` / ``CC_COPILOT_BACKEND``. If none is
available, callers fall back to the deterministic brief.
"""

from __future__ import annotations

import os
import time

from .brief import render
from .backends import resolve, Backend, BackendError
from .redact import redact

_HISTORY_CHARS = 16000


def _retry_attempts() -> int:
    """Total backend attempts for transient model failures.

    ``CC_COPILOT_MODEL_RETRIES`` is extra retries, not total attempts. The default
    is two retries because the common failure mode here is a flaky API connection,
    not a bad prompt.
    """
    raw = os.environ.get("CC_COPILOT_MODEL_RETRIES", "2").strip()
    try:
        retries = int(raw)
    except ValueError:
        retries = 2
    return max(1, min(6, 1 + max(0, retries)))


def _retry_delay(attempt: int) -> float:
    return min(4.0, 0.35 * (2 ** max(0, attempt)))


def _retryable_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    if any(code in msg for code in ("http 400", "http 401", "http 403",
                                    "http 404", "http 422")):
        return False
    needles = (
        "connection error", "request failed", "timed out", "timeout",
        "stream stalled", "remote end closed", "temporarily unavailable",
        "overloaded", "rate limit", "http 429", "http 500", "http 502",
        "http 503", "http 504", "http 529", "econnreset", "connection reset",
        "reset by peer", "errno 104", "remote disconnected", "bad status line",
        "service unavailable", "gateway timeout",
    )
    if not isinstance(exc, BackendError):
        # Raw socket/protocol exceptions occasionally escape a backend wrapper.
        # Retry only when their message still matches a known transient shape.
        return any(n in msg for n in needles)
    return any(n in msg for n in needles)


def _raise_retry_exhausted(exc: Exception, attempts: int):
    if attempts > 1 and _retryable_error(exc):
        raise BackendError(f"{exc} (after {attempts} attempts)") from exc
    raise exc


def _complete_with_retries(be: Backend, prompt: str, model: str = None,
                           timeout: int = 180) -> str:
    attempts = _retry_attempts()
    last = None
    for i in range(attempts):
        try:
            return be.complete(prompt, model=model, timeout=timeout)
        except Exception as e:
            last = e
            if i >= attempts - 1 or not _retryable_error(e):
                _raise_retry_exhausted(e, attempts)
            time.sleep(_retry_delay(i))
    raise last


def _stream_with_retries(be: Backend, prompt: str, model: str = None,
                         timeout: int = 180):
    attempts = _retry_attempts()
    last = None
    for i in range(attempts):
        got = False
        try:
            for chunk in be.stream(prompt, model=model, timeout=timeout):
                got = True
                yield chunk
            return
        except Exception as e:
            last = e
            if got:
                raise
            if i >= attempts - 1 or not _retryable_error(e):
                _raise_retry_exhausted(e, attempts)
            time.sleep(_retry_delay(i))
    raise last

_PREAMBLE_HEAD = """You are cc-copilot, a read-only cockpit agent for supervising \
coding agents. Below is an EVIDENCE CONTEXT PACK assembled from observable \
session history and bounded read-only project facts. Citations may be session \
transcript lines (`[L<n>]` or `[session:L<n>]`), project file lines \
(`[path:L<n>]`), or deterministic collector facts (`[tree]`, `[git:*]`).

STRICT RULES:
- Use the evidence context as the source for observed facts. Do NOT invent \
files, commands, errors, statuses, or actions that aren't in it.
- Keep citations when you state observed facts.
- You may synthesize, judge risk, and recommend next steps when grounded in \
the cited evidence; label hypotheses as inference.
- If evidence is insufficient, state the missing observed evidence without \
referring to internal packet names.
- Do not mention internal packet names unless the user asks about sources.
- Answer in the user's language. Be concise and concrete."""

# The tools clause follows the narrator sandbox (config.narrator_sandbox()):
# read-only (default) forbids tool use; unconfined allows it, so the prompt must
# not contradict the CLI flags that opt the narrator into tool use. The head is
# identical for both so locate.is_own_session still recognizes our narration
# transcripts (signature: "read-only cockpit agent for supervising coding agents").
_TOOLS_READONLY = " Prose only; do not use any tools or read any files."
_TOOLS_UNCONFINED = (" You may use your tools to read files and investigate when "
                     "the evidence context is insufficient.")


def _preamble() -> str:
    from . import config
    if config.narrator_sandbox() == "unconfined":
        return _PREAMBLE_HEAD + _TOOLS_UNCONFINED
    return _PREAMBLE_HEAD + _TOOLS_READONLY


# Kept for backward compatibility with anything that imported the constant.
_PREAMBLE = _PREAMBLE_HEAD + _TOOLS_READONLY

_NARRATE_TASK = (
    "Orient the returning human in 3–4 sentences: what did this agent do while "
    "they were away, and does it look safe to let it keep running (use the Safety "
    "verdict)? Point them at the observed evidence that most warrants a look, but "
    "do NOT prescribe the next action to take. Keep citations for specific "
    "observed claims."
)


def _be(backend) -> Backend:
    return backend if isinstance(backend, Backend) else resolve(backend)


def available(backend=None) -> bool:
    # A probe must never crash a caller: resolving/probing a backend can fail
    # outside BackendError (e.g. an unusable TMPDIR while building the registry).
    # Any failure means "not usable" → callers fall back to the deterministic core.
    try:
        return _be(backend).available()
    except Exception:
        return False


def backend_name(backend=None) -> str:
    try:
        return _be(backend).describe()
    except BackendError as e:
        return str(e)


def _as_evidence_context(brief_text: str) -> str:
    """Remove "brief" identity cues before sending deterministic text to an LLM."""
    lines = []
    for line in brief_text.splitlines():
        if line.startswith("# cc-copilot multi-session brief"):
            line = line.replace("# cc-copilot multi-session brief",
                                "# cc-copilot multi-session evidence context", 1)
        elif line.startswith("# 🛰  cc-copilot brief"):
            line = line.replace("# 🛰  cc-copilot brief",
                                "# cc-copilot evidence context", 1)
        elif line.startswith("# cc-copilot brief"):
            line = line.replace("# cc-copilot brief",
                                "# cc-copilot evidence context", 1)
        lines.append(line)
    return "\n".join(lines)


_DEFAULT_TURN_TASK = (
    "Execute the TASK above using only the EVIDENCE CONTEXT above. Keep "
    "citations for observed facts, label inference, and say what evidence is "
    "missing when the context is insufficient."
)


def _prompt(brief_text: str, task: str, turn_task: str = None) -> str:
    # Invariant A: scrub secret-shaped content from the model-bound copy at the
    # single narration chokepoint. Every model call (run_brief / *_stream / ask
    # / chat / recap / next-step) funnels through here, so redacting the composed
    # prompt covers all of them — evidence, embedded project excerpts, and chat
    # history alike. The on-disk transcript, the [L<n>] line map, and what the
    # cockpit shows the human locally are untouched (this copy only ever leaves
    # for the backend).
    task = str(task or "").strip()
    turn_task = str(turn_task or _DEFAULT_TURN_TASK).strip()
    return redact(
        _preamble()
        + "\n\n=== TASK (stable instructions) ===\n"
        + task
        + "\n=== END TASK ===\n"
        + "\n\n=== EVIDENCE CONTEXT (observed facts and citations) ===\n"
        + _as_evidence_context(brief_text)
        + "\n=== END EVIDENCE CONTEXT ===\n\n"
        + "=== CURRENT TURN ===\n"
        + turn_task)


def _with_instruction(task: str, instruction: str = "") -> str:
    """Fold a returning-human instruction (`/now in spanish`, `/since just the
    blocker`, `… as bullets`) into a recap/recommend task. The instruction
    shapes HOW the grounded answer reads — language, tone, length, focus — but
    the grounding contract is restated so it can never license inventing facts
    beyond the cited evidence."""
    instruction = (instruction or "").strip()
    if not instruction:
        return task
    return (task + "\n\nThe returning human added an instruction for how to "
            'answer: "' + instruction + '". Honor it for language, tone, '
            "length, or focus — but stay grounded in the evidence above and "
            "keep the [L…] citations; never invent facts to satisfy it.")


def _turn_with_instruction(instruction: str = "") -> str:
    instruction = (instruction or "").strip()
    if not instruction:
        return _DEFAULT_TURN_TASK
    return (_DEFAULT_TURN_TASK + "\n\nThe returning human added an instruction "
            'for how to answer: "' + instruction + '". Honor it for language, '
            "tone, length, or focus — but stay grounded in the evidence above "
            "and keep the [L…] citations; never invent facts to satisfy it.")


def run(state, task: str, model: str = None, backend=None, timeout: int = 180) -> str:
    return run_brief(render(state), task, model=model, backend=backend, timeout=timeout)


def run_brief(brief_text: str, task: str, model: str = None,
              backend=None, timeout: int = 180, turn_task: str = None) -> str:
    be = _be(backend)
    if not be.available():
        raise RuntimeError(f"backend '{be.name}' unavailable — {be.reason()}. "
                           f"Try `cc-copilot backends` to see your options.")
    return _complete_with_retries(be, _prompt(brief_text, task, turn_task=turn_task),
                                  model=model, timeout=timeout)


class StreamHandle:
    """Iterable wrapper around a backend stream.

    Iterate it to receive answer chunks; when iteration finishes (or aborts),
    ``text`` holds everything emitted so far (stripped) and ``usage`` holds the
    backend's exact :class:`~cccopilot.backends.Usage` if it reported one.
    Construction is non-blocking — all backend work happens during iteration,
    so it is safe to build on the UI thread and consume on a worker."""

    def __init__(self, be: Backend, gen):
        self._be = be
        self._gen = gen
        self.text = ""
        self.usage = None
        self.done = False
        self.cancelled = False

    def __iter__(self):
        parts = []
        try:
            if self.cancelled:
                return              # cancelled before iteration → never start the
                                    # backend (the pre-start /stop window)
            for chunk in self._gen:
                if self.cancelled:
                    break           # mid-stream stop, or a non-streaming fallback
                                    # whose one blocking chunk we now suppress
                parts.append(chunk)
                yield chunk
        finally:
            self.text = "".join(parts).strip()
            self.usage = getattr(self._be, "last_usage", None)
            self.done = True

    def cancel(self):
        """Best-effort, thread-safe abort. Sets ``cancelled`` (so a cancel that
        races BEFORE iteration starts, or a non-streaming fallback, still stops
        cleanly) AND kills the transport — the consuming thread is usually blocked
        INSIDE the backend read, so killing it makes that read return at once."""
        self.cancelled = True
        try:
            getattr(self._be, "cancel", lambda: None)()
        except Exception:
            pass


def stream_enabled() -> bool:
    return os.environ.get("CC_COPILOT_STREAM", "").strip().lower() not in (
        "0", "false", "no", "off")


def run_brief_stream(brief_text: str, task: str, model: str = None,
                     backend=None, timeout: int = 180,
                     turn_task: str = None) -> StreamHandle:
    """Streaming sibling of :func:`run_brief` — same grounding contract, the
    answer just arrives in chunks. ``CC_COPILOT_STREAM=0`` forces the blocking
    single-chunk path."""
    be = _be(backend)
    if not be.available():
        raise RuntimeError(f"backend '{be.name}' unavailable — {be.reason()}. "
                           f"Try `cc-copilot backends` to see your options.")
    prompt = _prompt(brief_text, task, turn_task=turn_task)
    if not stream_enabled():
        def _one():
            yield _complete_with_retries(be, prompt, model=model, timeout=timeout)
        return StreamHandle(be, _one())
    return StreamHandle(be, _stream_with_retries(be, prompt, model=model,
                                                 timeout=timeout))


def narrate_brief_stream(brief_text: str, model: str = None, backend=None) -> StreamHandle:
    return run_brief_stream(brief_text, _NARRATE_TASK, model=model, backend=backend)


_SINCE_RECAP_TASK = (
    "The evidence below is the DELTA of everything that changed since the "
    "returning human last looked at this agent. Recap it for them in 3–5 "
    "sentences: what the agent did (asks answered, commands run, failures, files "
    "changed), whether it looks safe to let it keep running (use any Safety "
    "transition shown), and which change most warrants a closer look. Recap and "
    "orient only — do NOT prescribe the next action to take. Use ONLY this "
    "evidence; keep the [L…] citations for specific observed claims. If the "
    "evidence shows nothing changed, say so in one line."
)


def recap_since(since_text: str, model: str = None, backend=None,
                instruction: str = "") -> str:
    """Narrate a deterministic ``/since`` delta into a grounded re-entry recap.

    Same faithful contract as :func:`narrate` — the model sees only the cited
    delta and keeps its ``[L…]`` citations; it does not invent. ``instruction``
    is an optional free-text steer (`/since in spanish`) that shapes the wording
    without loosening the grounding."""
    return run_brief(since_text, _SINCE_RECAP_TASK, model=model, backend=backend,
                     turn_task=_turn_with_instruction(instruction))


_WATCH_PROGRESS_TASK = (
    "The evidence below is a small WATCH DELTA from a coding-agent session that "
    "the human asked cc-copilot to follow. Turn it into a readable process "
    "update, not an event log. In 1-3 concise sentences, explain what appears "
    "to be happening now, how the work progressed since the last watch update, "
    "and whether anything needs attention. Use ONLY the cited evidence; keep "
    "citations for observed facts. Do not list every file or event. Do not "
    "recommend a new instruction unless the evidence shows a blocker/failure; "
    "if there is a blocker, name it plainly."
)


def watch_progress_brief(delta_text: str, model: str = None, backend=None,
                         instruction: str = "") -> str:
    """Narrate a small watch delta into a process-oriented progress update."""
    return run_brief(delta_text, _WATCH_PROGRESS_TASK, model=model, backend=backend,
                     turn_task=_turn_with_instruction(instruction))


_WATCH_FLOW_TASK = (
    "The evidence below is the WATCH FLOW CONTEXT for a coding-agent session. "
    "It includes the baseline before watch started, the previous Now update, "
    "the current watch step, and the newest observed delta. Produce the next "
    "Now update and decide whether the newest delta still belongs to the "
    "current semantic step or should start the next step. The Now update should "
    "be a short process-status sentence or two, not a command echo and not an "
    "event log. Prefer SAME unless the agent meaningfully changes intent or "
    "work phase. Use NEW when the current step should be closed and digested: "
    "starting a new implementation area, moving from editing to verification, "
    "switching from verification to fixing failures, reaching completion, or "
    "needing human attention. Use ONLY the cited evidence; keep citations in "
    "the now line for observed facts. Return exactly these machine-readable "
    "lines and nothing else:\n"
    "now: short readable process update, 1-2 sentences\n"
    "action: same|new\n"
    "title: short human title, 2-7 words\n"
    "phase: planning|editing|building|testing|debugging|reviewing|blocked|complete|running|other\n"
    "reason: short reason for the boundary decision\n"
    "attention: none or short attention note"
)


def watch_flow_update(flow_text: str, model: str = None, backend=None,
                      instruction: str = "") -> str:
    """Produce a watch Now update and semantic step-boundary decision together."""
    return run_brief(flow_text, _WATCH_FLOW_TASK, model=model, backend=backend,
                     turn_task=_turn_with_instruction(instruction))


_WATCH_STEP_DECISION_TASK = (
    "The evidence below contains the CURRENT WATCH STEP and a NEW WATCH DELTA "
    "from a coding-agent session. Decide whether the new delta belongs on the "
    "current step card or should start a new semantic step. Prefer SAME when the "
    "agent is continuing the same unit of work, even if raw status labels change. "
    "Use NEW only for a meaningful shift in intent or work phase: starting a new "
    "implementation area, moving from editing to verification, switching from "
    "verification to fixing failures, reaching completion, or needing human "
    "attention. Return exactly these machine-readable lines and nothing else:\n"
    "action: same|new\n"
    "title: short human title, 2-7 words\n"
    "phase: planning|editing|building|testing|debugging|reviewing|blocked|complete|running|other\n"
    "reason: short reason for the boundary decision\n"
    "attention: none or short attention note"
)


def watch_step_decision(step_text: str, model: str = None, backend=None,
                        instruction: str = "") -> str:
    """Decide whether a watch delta starts a new semantic monitor step."""
    return run_brief(step_text, _WATCH_STEP_DECISION_TASK, model=model, backend=backend,
                     turn_task=_turn_with_instruction(instruction))


_WATCH_DIGEST_TASK = (
    "The evidence below is a WATCH STEP DIGEST BUFFER from a coding-agent "
    "session that cc-copilot has been following after explicit human opt-in. "
    "This digest is posterior: it closes the step that just ended before the "
    "watch monitor moves to the next semantic step. Write a readable monitoring "
    "digest in 3-5 concise sentences. Summarize the step's meaningful progress, "
    "important decisions or file changes, verification/failures/retries, and "
    "whether the human needs to act now. Use ONLY the cited evidence. Keep "
    "citations for observed facts. Do not produce an event log, do not list "
    "every changed file, and do not invent percent complete."
)


def watch_digest_brief(buffer_text: str, model: str = None, backend=None,
                       instruction: str = "") -> str:
    """Narrate accumulated watch evidence into a posterior step digest."""
    return run_brief(buffer_text, _WATCH_DIGEST_TASK, model=model, backend=backend,
                     turn_task=_turn_with_instruction(instruction))


_NEXT_STEP_TASK = (
    "The evidence below is the work this coding agent has just completed, plus "
    "its current status. The returning human wants to know what to do NEXT. "
    "Recommend the next step in 2–4 sentences: lead with any blocker that must "
    "clear first (a failure, an unanswered human turn, a stalled or mid-run "
    "agent), then give ONE concrete primary next action — the instruction to "
    "give the agent next, or what to verify / run / commit yourself — and at "
    "most one alternative. Ground every recommendation in the cited evidence and "
    "keep the [L…] citations. If the agent is still mid-run, say to let it "
    "finish rather than inventing busywork. Be concrete and actionable; no "
    "preamble, no recap of what already happened."
)


def next_step_brief(brief_text: str, model: str = None, backend=None,
                    instruction: str = "") -> str:
    """Recommend the next step from a deterministic evidence recap.

    Same faithful contract as :func:`narrate`: the model sees only the cited
    evidence and keeps its ``[L…]`` citations; it recommends, it doesn't invent.
    ``instruction`` is an optional free-text steer (`/now in spanish`) that
    shapes the wording without loosening the grounding."""
    return run_brief(brief_text, _NEXT_STEP_TASK, model=model, backend=backend,
                     turn_task=_turn_with_instruction(instruction))


def next_step_brief_stream(brief_text: str, model: str = None, backend=None,
                           instruction: str = "") -> StreamHandle:
    """Streaming sibling of :func:`next_step_brief` — identical grounding."""
    return run_brief_stream(brief_text, _NEXT_STEP_TASK, model=model,
                            backend=backend,
                            turn_task=_turn_with_instruction(instruction))


_GOAL_DRAFT_TASK = (
    "The evidence below combines the observed coding-agent session, cockpit "
    "conversation context, and bounded read-only project facts. Draft ONE "
    "paste-ready `/goal ...` command for the observed coding agent. The command "
    "must be suitable for Claude Code or Codex and should help the agent keep "
    "working until a verifiable end state is true. Include: the desired outcome, "
    "the verification surface (tests/build/benchmark/artifact/source audit), "
    "constraints or boundaries, and an explicit blocked stop condition. Do not "
    "invent files, commands, failures, or project facts. The slash command itself "
    "should not include citations, but after the command add a short 'Why this "
    "goal' section with cited evidence. Also state that cc-copilot generated the "
    "command but did not inject it into the agent. Keep the `/goal` command under "
    "4,000 characters."
)


def goal_brief(brief_text: str, model: str = None, backend=None,
               instruction: str = "") -> str:
    """Draft a paste-ready agent ``/goal`` command from evidence context."""
    return run_brief(brief_text, _GOAL_DRAFT_TASK, model=model, backend=backend,
                     turn_task=_turn_with_instruction(instruction))


def goal_brief_stream(brief_text: str, model: str = None, backend=None,
                      instruction: str = "") -> StreamHandle:
    """Streaming sibling of :func:`goal_brief` — identical grounding."""
    return run_brief_stream(brief_text, _GOAL_DRAFT_TASK, model=model,
                            backend=backend,
                            turn_task=_turn_with_instruction(instruction))


_LOOP_DRAFT_TASK = (
    "The evidence below combines the observed coding-agent session, cockpit "
    "conversation context, and bounded read-only project facts. Draft ONE "
    "paste-ready `/loop ...` command for the observed coding agent. Treat loop "
    "engineering as designing the recurring prompt, memory/verification boundary, "
    "cadence, and stop condition that replace repeated manual nudges. Prefer "
    "Claude Code's self-paced shape (`/loop <prompt>`) when the task should decide "
    "its own next wakeup from the observed state. Use a fixed interval "
    "(`/loop 5m <prompt>`) only when the human explicitly asks for a cadence or "
    "the evidence shows a polling task such as CI, deploy, logs, or a long command. "
    "The loop prompt must tell the agent what to inspect each iteration, what to "
    "do when it finds work, what to report when quiet, what not to do without "
    "authorization, and when to stop scheduling itself or ask the human. Do not "
    "invent files, commands, failures, URLs, PRs, or project facts. The slash "
    "command itself should not include citations, but after the command add a "
    "short 'Why this loop' section with cited evidence. Also state that cc-copilot "
    "generated the command but did not inject it into the agent. Keep the `/loop` "
    "command under 4,000 characters."
)


def loop_brief(brief_text: str, model: str = None, backend=None,
               instruction: str = "") -> str:
    """Draft a paste-ready agent ``/loop`` command from evidence context."""
    return run_brief(brief_text, _LOOP_DRAFT_TASK, model=model, backend=backend,
                     turn_task=_turn_with_instruction(instruction))


def loop_brief_stream(brief_text: str, model: str = None, backend=None,
                      instruction: str = "") -> StreamHandle:
    """Streaming sibling of :func:`loop_brief` — identical grounding."""
    return run_brief_stream(brief_text, _LOOP_DRAFT_TASK, model=model,
                            backend=backend,
                            turn_task=_turn_with_instruction(instruction))


def ask(state, question: str, model: str = None, backend=None) -> str:
    return ask_brief(render(state), question, model=model, backend=backend)


_ASK_TASK = (
    "Answer the returning human's current question as cc-copilot. Use cited "
    "evidence for observed facts. Synthesize or recommend when grounded; label "
    "inference. If the answer needs unavailable evidence, name what is missing."
)


def _ask_turn(question: str) -> str:
    return ('Current question from the returning human: "'
            + question.strip() + '"\n'
            + _DEFAULT_TURN_TASK)


def _ask_task(question: str) -> str:
    """Backward-compatible single-block ask task for tests/extensions."""
    return _ASK_TASK + "\n\n" + _ask_turn(question)


def ask_brief(brief_text: str, question: str, model: str = None, backend=None) -> str:
    return run_brief(brief_text, _ASK_TASK, model=model, backend=backend,
                     turn_task=_ask_turn(question))


def ask_brief_stream(brief_text: str, question: str, model: str = None,
                     backend=None) -> StreamHandle:
    return run_brief_stream(brief_text, _ASK_TASK, model=model, backend=backend,
                            turn_task=_ask_turn(question))


def _history_by_budget(history, max_chars: int = _HISTORY_CHARS) -> str:
    if not history:
        return ""
    parts = []
    used = 0
    omitted = 0
    entries = list(history)
    for idx in range(len(entries) - 1, -1, -1):
        role, text = entries[idx]
        block = ("User: " if role == "user" else "You: ") + str(text or "")
        if not parts and len(block) > max_chars:
            block = block[-max_chars:].lstrip()
        cost = len(block) + 1
        if parts and used + cost > max_chars:
            omitted = idx + 1
            break
        parts.append(block)
        used += cost
    parts.reverse()
    note = (f"{omitted} older message(s) omitted by chat-history budget.\n"
            if omitted else "")
    return note + "\n".join(parts)


def chat(state, history, question: str, model: str = None, backend=None) -> str:
    return chat_brief(render(state), history, question, model=model, backend=backend)


def _scope_guidance(brief_text: str) -> str:
    text = str(brief_text or "")
    if "scope: `multi-session`" in text or "multi-session evidence context" in text:
        return (
            "Scope guidance: this answer is grounded in multiple agent sessions. "
            "Do not flatten them into one event stream. When more than one "
            "session matters, compare by session label, call out blockers/risks "
            "and ownership, and lead with the decision the human can make next. "
            "Keep citations attached to each session-specific claim."
        )
    if "scope: `project`" in text:
        return (
            "Scope guidance: this answer is grounded in project-wide evidence. "
            "Use session facts, git/project facts, and file excerpts together. "
            "Group claims by session or project area when useful, call out "
            "cross-session risks, and lead with the decision the human can make "
            "next. Keep citations on observed facts."
        )
    return ""


_CHAT_TASK = (
    "Answer the current cockpit chat turn as cc-copilot. The current evidence "
    "context is the only source of new observed facts. Prior cockpit turns, when "
    "supplied, are continuity only; do not treat old answer prose as fresh "
    "evidence. Keep citations for observed facts, synthesize or recommend only "
    "when grounded, label inference, and name missing evidence when needed."
)


def _chat_turn(history, question: str, brief_text: str = "") -> str:
    convo = ""
    if history:
        convo = ("PRIOR TURNS (your earlier grounded answers — reference for "
                 "continuity, but the current evidence context above is the "
                 "source of new observed facts):\n"
                 + _history_by_budget(history) + "\n\n")
    scope = _scope_guidance(brief_text)
    if scope:
        scope += "\n\n"
    return (convo
            + 'Current question from the returning human: "'
            + question.strip() + '"\n'
            + scope
            + "Answer as cc-copilot. Use the current evidence context for "
            "observed facts and keep citations. Synthesize or recommend when "
            "grounded; label inference. If the answer needs unavailable "
            "evidence, name what is missing.")


def _chat_task(history, question: str, brief_text: str = "") -> str:
    """Backward-compatible single-block chat task for tests/extensions."""
    return _CHAT_TASK + "\n\n" + _chat_turn(history, question, brief_text)


def chat_brief(brief_text: str, history, question: str, model: str = None, backend=None) -> str:
    """Multi-turn sibling of :func:`ask` for the live chat sidecar.

    The current evidence context (re-read this turn and included by
    :func:`run_brief`) is the only source of new observed facts. Prior turns are
    replayed as *already-grounded* answers — referenced for continuity, never
    treated as fresh evidence — so a later answer cannot launder an un-cited
    claim from an earlier one.
    """
    return run_brief(brief_text, _CHAT_TASK, model=model, backend=backend,
                     turn_task=_chat_turn(history, question, brief_text))


def chat_brief_stream(brief_text: str, history, question: str, model: str = None,
                      backend=None) -> StreamHandle:
    """Streaming :func:`chat_brief` — identical grounding, chunked delivery."""
    return run_brief_stream(brief_text, _CHAT_TASK, model=model, backend=backend,
                            turn_task=_chat_turn(history, question, brief_text))
