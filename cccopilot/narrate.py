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

from .brief import render
from .backends import resolve, Backend, BackendError

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
    "Orient the returning human in 3–5 sentences: what did this agent do while "
    "they were away, does it look safe to let it keep running (use the Safety "
    "verdict), and the single most important thing to look at next? "
    "Keep citations for specific observed claims."
)


def _be(backend) -> Backend:
    return backend if isinstance(backend, Backend) else resolve(backend)


def available(backend=None) -> bool:
    try:
        return _be(backend).available()
    except BackendError:
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
    return (_PREAMBLE
            + "\n\n=== EVIDENCE CONTEXT (observed facts and citations) ===\n"
            + _as_evidence_context(brief_text)
            + "\n=== END EVIDENCE CONTEXT ===\n\n" + task)


def run(state, task: str, model: str = None, backend=None, timeout: int = 180) -> str:
    return run_brief(render(state), task, model=model, backend=backend, timeout=timeout)


def run_brief(brief_text: str, task: str, model: str = None,
              backend=None, timeout: int = 180) -> str:
    be = _be(backend)
    if not be.available():
        raise RuntimeError(f"backend '{be.name}' unavailable — {be.reason()}. "
                           f"Try `cc-copilot backends` to see your options.")
    return be.complete(_prompt(brief_text, task), model=model, timeout=timeout)


def narrate(state, model: str = None, backend=None) -> str:
    return run(state, _NARRATE_TASK, model=model, backend=backend)


def narrate_brief(brief_text: str, model: str = None, backend=None) -> str:
    return run_brief(brief_text, _NARRATE_TASK, model=model, backend=backend)


def ask(state, question: str, model: str = None, backend=None) -> str:
    return ask_brief(render(state), question, model=model, backend=backend)


def ask_brief(brief_text: str, question: str, model: str = None, backend=None) -> str:
    task = ('The returning human asks: "' + question.strip() + '"\n'
            "Answer as cc-copilot. Use cited evidence for observed facts. "
            "Synthesize or recommend when grounded; label inference. "
            "If the answer needs unavailable evidence, name what is missing.")
    return run_brief(brief_text, task, model=model, backend=backend)


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


def chat_brief(brief_text: str, history, question: str, model: str = None, backend=None) -> str:
    """Multi-turn sibling of :func:`ask` for the live chat sidecar.

    The current evidence context (re-read this turn, prepended by
    :func:`run_brief`) is the only source of new observed facts. Prior turns are
    replayed as *already-grounded* answers — referenced for continuity, never
    treated as fresh evidence — so a later answer cannot launder an un-cited
    claim from an earlier one.
    """
    convo = ""
    if history:
        convo = ("PRIOR TURNS (your earlier grounded answers — reference for "
                 "continuity, but the current evidence context above is the "
                 "source of new observed facts):\n"
                 + _history_by_budget(history) + "\n\n")
    task = (convo + 'Current question from the returning human: "'
            + question.strip() + '"\n'
            "Answer as cc-copilot. Use the current evidence context for "
            "observed facts and keep citations. Synthesize or recommend when "
            "grounded; label inference. If the answer needs unavailable "
            "evidence, name what is missing.")
    return run_brief(brief_text, task, model=model, backend=backend)
