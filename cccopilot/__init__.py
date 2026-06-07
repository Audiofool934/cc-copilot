"""cc-copilot — a read-only shadow-memory sidecar for Claude Code sessions.

Reads a Claude Code session transcript (the local JSONL ledger) and
reconstructs, *deterministically and with evidence*, what the agent has been
doing — so a human who stepped away can come back and ask "what happened,
is it stuck, what should I look at?" without scrolling the transcript.

The design rule that makes this a copilot and not a second hallucinating
agent: every statement in a brief points back to a concrete transcript event
(a JSONL line number + timestamp). The deterministic core never guesses.
"""

__version__ = "0.4.0"
