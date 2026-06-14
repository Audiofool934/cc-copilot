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

from .brief import render
from .backends import resolve, Backend, BackendError
from .redact import redact

_HISTORY_CHARS = 16000

_PREAMBLE = """You are cc-copilot, a read-only cockpit agent for supervising \
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
- Answer in the user's language. Be concise and concrete. Prose only; do not \
use any tools or read any files.\
"""

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


def _prompt(brief_text: str, task: str) -> str:
    # Invariant A: scrub secret-shaped content from the model-bound copy at the
    # single narration chokepoint. Every model call (run_brief / *_stream / ask
    # / chat / recap / next-step) funnels through here, so redacting the composed
    # prompt covers all of them — evidence, embedded project excerpts, and chat
    # history alike. The on-disk transcript, the [L<n>] line map, and what the
    # cockpit shows the human locally are untouched (this copy only ever leaves
    # for the backend).
    return redact(
        _PREAMBLE
        + "\n\n=== EVIDENCE CONTEXT (observed facts and citations) ===\n"
        + _as_evidence_context(brief_text)
        + "\n=== END EVIDENCE CONTEXT ===\n\n" + task)


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


def run(state, task: str, model: str = None, backend=None, timeout: int = 180) -> str:
    return run_brief(render(state), task, model=model, backend=backend, timeout=timeout)


def run_brief(brief_text: str, task: str, model: str = None,
              backend=None, timeout: int = 180) -> str:
    be = _be(backend)
    if not be.available():
        raise RuntimeError(f"backend '{be.name}' unavailable — {be.reason()}. "
                           f"Try `cc-copilot backends` to see your options.")
    return be.complete(_prompt(brief_text, task), model=model, timeout=timeout)


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

    def __iter__(self):
        parts = []
        try:
            for chunk in self._gen:
                parts.append(chunk)
                yield chunk
        finally:
            self.text = "".join(parts).strip()
            self.usage = getattr(self._be, "last_usage", None)
            self.done = True

    def cancel(self):
        """Best-effort, thread-safe abort. The consuming thread is usually
        blocked INSIDE the backend read — this kills the transport so that
        read returns and the stream unwinds immediately (see Backend.cancel)."""
        try:
            getattr(self._be, "cancel", lambda: None)()
        except Exception:
            pass


def stream_enabled() -> bool:
    return os.environ.get("CC_COPILOT_STREAM", "").strip().lower() not in (
        "0", "false", "no", "off")


def run_brief_stream(brief_text: str, task: str, model: str = None,
                     backend=None, timeout: int = 180) -> StreamHandle:
    """Streaming sibling of :func:`run_brief` — same grounding contract, the
    answer just arrives in chunks. ``CC_COPILOT_STREAM=0`` forces the blocking
    single-chunk path."""
    be = _be(backend)
    if not be.available():
        raise RuntimeError(f"backend '{be.name}' unavailable — {be.reason()}. "
                           f"Try `cc-copilot backends` to see your options.")
    prompt = _prompt(brief_text, task)
    if not stream_enabled():
        def _one():
            yield be.complete(prompt, model=model, timeout=timeout)
        return StreamHandle(be, _one())
    return StreamHandle(be, be.stream(prompt, model=model, timeout=timeout))


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
    return run_brief(since_text, _with_instruction(_SINCE_RECAP_TASK, instruction),
                     model=model, backend=backend)


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
    return run_brief(brief_text, _with_instruction(_NEXT_STEP_TASK, instruction),
                     model=model, backend=backend)


def next_step_brief_stream(brief_text: str, model: str = None, backend=None,
                           instruction: str = "") -> StreamHandle:
    """Streaming sibling of :func:`next_step_brief` — identical grounding."""
    return run_brief_stream(brief_text, _with_instruction(_NEXT_STEP_TASK, instruction),
                            model=model, backend=backend)


def ask(state, question: str, model: str = None, backend=None) -> str:
    return ask_brief(render(state), question, model=model, backend=backend)


def _ask_task(question: str) -> str:
    return ('The returning human asks: "' + question.strip() + '"\n'
            "Answer as cc-copilot. Use cited evidence for observed facts. "
            "Synthesize or recommend when grounded; label inference. "
            "If the answer needs unavailable evidence, name what is missing.")


def ask_brief(brief_text: str, question: str, model: str = None, backend=None) -> str:
    return run_brief(brief_text, _ask_task(question), model=model, backend=backend)


def ask_brief_stream(brief_text: str, question: str, model: str = None,
                     backend=None) -> StreamHandle:
    return run_brief_stream(brief_text, _ask_task(question), model=model, backend=backend)


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


def _chat_task(history, question: str) -> str:
    convo = ""
    if history:
        convo = ("PRIOR TURNS (your earlier grounded answers — reference for "
                 "continuity, but the current evidence context above is the "
                 "source of new observed facts):\n"
                 + _history_by_budget(history) + "\n\n")
    return (convo + 'Current question from the returning human: "'
            + question.strip() + '"\n'
            "Answer as cc-copilot. Use the current evidence context for "
            "observed facts and keep citations. Synthesize or recommend when "
            "grounded; label inference. If the answer needs unavailable "
            "evidence, name what is missing.")


def chat_brief(brief_text: str, history, question: str, model: str = None, backend=None) -> str:
    """Multi-turn sibling of :func:`ask` for the live chat sidecar.

    The current evidence context (re-read this turn, prepended by
    :func:`run_brief`) is the only source of new observed facts. Prior turns are
    replayed as *already-grounded* answers — referenced for continuity, never
    treated as fresh evidence — so a later answer cannot launder an un-cited
    claim from an earlier one.
    """
    return run_brief(brief_text, _chat_task(history, question), model=model, backend=backend)


def chat_brief_stream(brief_text: str, history, question: str, model: str = None,
                      backend=None) -> StreamHandle:
    """Streaming :func:`chat_brief` — identical grounding, chunked delivery."""
    return run_brief_stream(brief_text, _chat_task(history, question),
                            model=model, backend=backend)
