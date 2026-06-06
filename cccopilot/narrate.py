"""Leg ③ — the grounded narration / observer-chat layer.

The whole point of cc-copilot is *not* being a second hallucinating agent. So
the LLM here never sees the raw transcript — it sees the **deterministic,
evidence-cited brief** produced by legs ①/② and is told to answer only from it,
keeping the ``[L…]`` citations. It narrates grounded facts; it doesn't invent.

Backend is the local ``claude`` CLI in print mode (no API key, uses your
existing auth). Override with ``CC_COPILOT_LLM_CMD`` (e.g. ``"llm -m gpt-4o"`` or
``"codex exec"``) — the prompt is appended as the final argument. If no backend
is available, callers fall back to the deterministic brief.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .brief import render

def _default_claude() -> str:
    """Prefer the canonical Claude Code CLI over any earlier-on-PATH wrapper
    (e.g. an app-bundled `claude`), so `-p` print mode behaves as expected."""
    cand = os.path.expanduser("~/.local/bin/claude")
    if os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    return shutil.which("claude") or "claude"

_PREAMBLE = """You are cc-copilot's narration layer. Below is a DETERMINISTIC, \
evidence-cited brief of a long-running coding agent's session — a read-only \
sidecar reconstructed it from the session transcript, and every [L<n>] marks a \
real transcript line.

STRICT RULES:
- Answer ONLY from the brief below. Do NOT invent files, commands, errors, \
statuses, or actions that aren't in it.
- Keep the [L<n>] citation when you state a specific fact.
- If the brief doesn't contain enough to answer, say so plainly — do not guess.
- Be concise and concrete. Prose only; do not use any tools or read any files.\
"""

_NARRATE_TASK = (
    "Brief the returning human in 3–5 sentences: what did this agent do while "
    "they were away, does it look safe to let it keep running (use the Safety "
    "verdict), and the single most important thing to look at next? "
    "Keep [L<n>] citations for specific claims."
)


def _cmd() -> list:
    env = os.environ.get("CC_COPILOT_LLM_CMD", "").strip()
    return env.split() if env else [_default_claude(), "-p"]


def available() -> bool:
    c = _cmd()[0]
    return os.path.isfile(c) or shutil.which(c) is not None


def backend_name() -> str:
    return " ".join(_cmd())


def _prompt(brief_text: str, task: str) -> str:
    return (_PREAMBLE
            + "\n\n=== BRIEF (the only ground truth you may use) ===\n"
            + brief_text
            + "\n=== END BRIEF ===\n\n" + task)


def run(state, task: str, model: str = None, timeout: int = 180) -> str:
    if not available():
        raise RuntimeError(
            f"no LLM backend on PATH (looked for `{_cmd()[0]}`). "
            f"Install the `claude` CLI or set CC_COPILOT_LLM_CMD.")
    cmd = _cmd()
    if model:
        cmd = cmd + ["--model", model]
    cmd = cmd + [_prompt(render(state), task)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"LLM backend timed out after {timeout}s")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"LLM backend exited {proc.returncode}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError("LLM backend returned no output")
    return out


def narrate(state, model: str = None) -> str:
    return run(state, _NARRATE_TASK, model=model)


def ask(state, question: str, model: str = None) -> str:
    task = ('The returning human asks: "' + question.strip() + '"\n'
            "Answer grounded in the brief, with [L<n>] citations. "
            "If the brief lacks the information, say so rather than guessing.")
    return run(state, task, model=model)


def chat(state, history, question: str, model: str = None) -> str:
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
            "Answer ONLY from the current brief above, keeping [L<n>] citations. "
            "If it lacks the info, say so plainly.")
    return run(state, task, model=model)
