"""The agent-source seam.

cc-copilot reconstructs a coding agent's working state from the per-session
JSONL ledger it leaves on disk. Every serious terminal agent (Claude Code,
Codex, …) writes one such ledger; they differ only in *where* the files live
and *how* each line is shaped. Everything below ``transcript`` already speaks a
single normalized model (``Transcript`` of ``Record``), so adding an agent only
requires supplying two things:

1. **discovery** — given a project cwd, which session files exist.
2. **parse** — turn one session file into a normalized ``Transcript``.

That contract is :class:`AgentSource`. The dispatcher in ``sources/__init__``
routes a path or a cwd to the right source, so the rest of the codebase stays
agent-agnostic.

This module is a leaf: it imports nothing from the concrete sources, so the
registry can be populated without import cycles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:  # avoid import cycles / keep this module a leaf
    from ..locate import SessionRef
    from ..transcript import Transcript


class AgentSource:
    """A read-only adapter over one agent's on-disk session transcripts.

    Subclasses are thin: discovery + parse + a few path helpers. They must never
    write to the agent's storage — the read-only contract spans every agent.
    """

    #: short, stable identifier used in config, ``--agent``, and ``SessionRef.agent``
    name: str = "agent"
    #: human-facing label for the cockpit/status surfaces
    label: str = "Agent"

    # ---- availability ---------------------------------------------------
    def available(self) -> bool:
        """True if this agent's storage exists on this machine.

        Lets the dispatcher silently skip sources whose home dir is absent (a
        Claude-only machine has no ``~/.codex``) without erroring.
        """
        raise NotImplementedError

    # ---- ownership ------------------------------------------------------
    def owns(self, path: str) -> bool:
        """True if ``path`` is one of *this* source's transcript files.

        Used to route :func:`parse` to the right adapter. Should be cheap
        (path/name inspection), not a file read.
        """
        raise NotImplementedError

    # ---- discovery ------------------------------------------------------
    def list_sessions(self, cwd: str, include_own: bool = False) -> "List[SessionRef]":
        """This agent's sessions for ``cwd``'s project, newest first."""
        raise NotImplementedError

    def projects_with_sessions(self, limit: int = 8) -> "List[Tuple[str, int, float]]":
        """``[(cwd, count, mtime)]`` across all of this agent's projects."""
        return []

    # ---- per-session ----------------------------------------------------
    def parse(self, path: str) -> "Transcript":
        """Parse one session file into a normalized ``Transcript``."""
        raise NotImplementedError

    def read_cwd(self, path: str) -> Optional[str]:
        """The project cwd this session ran in, read from the file."""
        return None

    def read_title(self, path: str, session_id: str = "") -> str:
        """A human title for the session, if the agent records one."""
        return ""


# ---- registry -----------------------------------------------------------
# Ordered most-specific first; the dispatcher resolves ownership in this order
# and treats the last entry (Claude) as the catch-all default.
_REGISTRY: "List[AgentSource]" = []


def register(source: AgentSource) -> AgentSource:
    _REGISTRY.append(source)
    return source


def all_sources() -> "List[AgentSource]":
    return list(_REGISTRY)
