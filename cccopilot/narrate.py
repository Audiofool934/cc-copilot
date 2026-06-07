"""Leg ③ — the grounded narration / observer-chat layer.

The whole point of cc-copilot is *not* being a second hallucinating agent. So
the LLM here never sees raw transcripts or ambient repo access — it sees a
**deterministic, evidence-cited brief** produced by legs ①/② (and, for project
scope, read-only file facts) and is told to answer only from it, keeping the
citations. It narrates grounded facts; it doesn't invent.

The backend is pluggable (see :mod:`cccopilot.backends`): a local agent CLI
(`claude`, `codex`, `gemini`, `llm`) or any OpenAI-compatible HTTP API
(`deepseek`, `openai`, `openrouter`, `ollama`, …). Default is `codex`. Pick one
with ``backend=...`` / ``--backend`` / ``CC_COPILOT_BACKEND``. If none is
available, callers fall back to the deterministic brief.
"""

from __future__ import annotations

from .brief import render
from .backends import resolve, Backend, BackendError

_PREAMBLE = """You are cc-copilot's narration layer. Below is a DETERMINISTIC, \
evidence-cited brief from a read-only sidecar. Citations may be session transcript \
lines (`[L<n>]` or `[session:L<n>]`), project file lines (`[path:L<n>]`), or \
deterministic collector facts (`[tree]`, `[git:*]`).

STRICT RULES:
- Answer ONLY from the brief below. Do NOT invent files, commands, errors, \
statuses, or actions that aren't in it.
- Keep the citation when you state a specific fact.
- If the brief doesn't contain enough to answer, say so plainly — do not guess.
- Be concise and concrete. Prose only; do not use any tools or read any files.\
"""

_NARRATE_TASK = (
    "Brief the returning human in 3–5 sentences: what did this agent do while "
    "they were away, does it look safe to let it keep running (use the Safety "
    "verdict), and the single most important thing to look at next? "
            "Keep citations for specific claims."
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


def _prompt(brief_text: str, task: str) -> str:
    return (_PREAMBLE
            + "\n\n=== BRIEF (the only ground truth you may use) ===\n"
            + brief_text
            + "\n=== END BRIEF ===\n\n" + task)


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
            "Answer grounded in the brief, with citations. "
            "If the brief lacks the information, say so rather than guessing.")
    return run_brief(brief_text, task, model=model, backend=backend)


def chat(state, history, question: str, model: str = None, backend=None) -> str:
    return chat_brief(render(state), history, question, model=model, backend=backend)


def chat_brief(brief_text: str, history, question: str, model: str = None, backend=None) -> str:
    """Multi-turn sibling of :func:`ask` for the live chat sidecar.

    The CURRENT brief (re-read this turn, prepended by :func:`run`) is the only
    source of new facts. Prior turns are replayed as *already-grounded* answers
    — referenced for continuity, never treated as fresh evidence — so a later
    answer cannot launder an un-cited claim from an earlier one.
    """
    convo = ""
    if history:
        parts = []
        for role, text in history[-8:]:
            parts.append(("User: " if role == "user" else "You: ") + text)
        convo = ("PRIOR TURNS (your earlier grounded answers — reference for "
                 "continuity, but the CURRENT brief above is the only source of "
                 "new facts):\n" + "\n".join(parts) + "\n\n")
    task = (convo + 'Current question from the returning human: "'
            + question.strip() + '"\n'
            "Answer ONLY from the current brief above, keeping citations. "
            "If it lacks the info, say so plainly.")
    return run_brief(brief_text, task, model=model, backend=backend)
