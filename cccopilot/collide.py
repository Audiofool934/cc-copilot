"""Cross-session collision radar — the same file mutated by 2+ different agent
sessions, especially on different git branches.

This is the capability only a read-only, cross-agent observer can produce: an
in-process agent has no handle to a sibling session, and neither vendor can read
the other's transcripts — so nothing *but* a tool that unions Claude Code and
Codex sessions by project cwd can see one agent clobbering a file another agent
is editing on a different branch. Deterministic, evidence-cited, never writes.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from . import sources as SRC, state as S

# A session older than this isn't live divergence — it's history. 3 days catches
# a branch you worked yesterday that conflicts with today's work (still unmerged,
# still a collision) while keeping months-old sessions out.
COLLISION_WINDOW_SECONDS = 72 * 3600


@dataclass
class Party:
    session_id: str
    agent: str
    branch: str
    status: str
    last_line: int
    last_hhmm: str
    last_ts: object = None     # full datetime — orders parties correctly across days


def _party_recency(p: "Party") -> float:
    """Full-timestamp sort key (HH:MM alone mis-sorts yesterday 23:50 vs today
    00:10, and /status only shows the first few parties)."""
    return p.last_ts.timestamp() if p.last_ts is not None else 0.0


@dataclass
class Collision:
    path: str                 # canonical (relative-to-cwd) path
    parties: list             # [Party], most-recent activity first
    cross_branch: bool        # 2+ distinct branches → divergence / merge-conflict risk


def _norm(path: str, cwd: str) -> str:
    """Canonicalize a transcript-recorded path to relative-to-cwd so the same
    file matches whether one session stored it absolute and another relative."""
    if not path:
        return path
    ap = path if os.path.isabs(path) else os.path.join(cwd, path)
    ap = os.path.normpath(ap)
    try:
        rel = os.path.relpath(ap, cwd)
    except ValueError:                      # different drive (Windows) — keep abs
        return ap
    return rel if not rel.startswith("..") else ap


def find_collisions(items, cwd: str):
    """Core (pure) fold: ``items`` is an iterable of ``(session_id, agent, State)``.
    Returns the files mutated by 2+ distinct sessions, cross-branch first."""
    by_path: dict = {}
    for sid, agent, st in items:
        if st.status == "empty":
            continue
        branch = (st.tr.git_branch or "").strip()
        for path, fc in st.files.items():
            np = _norm(path, cwd)
            by_path.setdefault(np, {})[sid] = Party(
                sid, agent, branch, st.status, fc.last_line, fc.last_hhmm,
                st.tr.last_ts)
    cols = []
    for path, parties in by_path.items():
        if len(parties) < 2:
            continue
        ps = sorted(parties.values(), key=_party_recency, reverse=True)
        cross = len({p.branch for p in ps if p.branch}) >= 2
        cols.append(Collision(path, ps, cross))
    # most actionable first: cross-branch divergence, then most-contended, then path
    cols.sort(key=lambda c: (not c.cross_branch, -len(c.parties), c.path))
    return cols


def collisions(cwd: str, window_seconds: float = COLLISION_WINDOW_SECONDS,
               now: float = None):
    """Discover the project's recent cross-agent sessions and fold them into the
    collision list. Read-only; parses each recent session once (on demand)."""
    now = time.time() if now is None else now
    items = []
    for r in SRC.list_sessions(cwd, include_own=False):
        if now - r.mtime > window_seconds:
            continue
        try:
            st = S.build(SRC.parse(r.path))
        except OSError:
            continue
        items.append((r.session_id, r.agent, st))
    return find_collisions(items, cwd)
