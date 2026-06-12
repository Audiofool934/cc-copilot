"""Claude Code adapter — the original, reference source.

This is a thin delegation layer over the existing ``locate`` (discovery) and
``transcript`` (parse) modules. Carving the seam must not change Claude Code
behavior, so this source adds no logic of its own — it forwards. It is also the
**catch-all default**: any path another source does not claim is parsed here.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from .. import locate
from .. import transcript as T
from ..locate import SessionRef
from ..transcript import Transcript
from .base import AgentSource


class ClaudeSource(AgentSource):
    name = "claude"
    label = "Claude Code"

    def available(self) -> bool:
        return os.path.isdir(locate.projects_root())

    def current_session_id(self) -> str:
        return locate.current_session_id()

    def current_session_path(self) -> Optional[str]:
        return locate.current_session_path()

    def owns(self, path: str) -> bool:
        # Claude transcripts live under ``<config>/projects/``. We treat Claude
        # as the default, so the dispatcher only consults this after the more
        # specific sources decline; an explicit check still helps when a Codex
        # and Claude home are unusually nested.
        try:
            root = os.path.abspath(locate.projects_root())
            return os.path.abspath(path).startswith(root + os.sep)
        except (OSError, ValueError):
            return False

    def list_sessions(self, cwd: str, include_own: bool = False) -> List[SessionRef]:
        refs = locate.list_sessions(cwd, include_own=include_own)
        for r in refs:
            r.agent = self.name
        return refs

    def projects_with_sessions(self, limit: int = 8) -> List[Tuple[str, int, float]]:
        return locate.projects_with_sessions(limit)

    def parse(self, path: str) -> Transcript:
        return T.parse(path)

    def read_cwd(self, path: str) -> Optional[str]:
        return locate.read_cwd(path)

    def read_title(self, path: str, session_id: str = "") -> str:
        return locate.read_title(path, session_id)
