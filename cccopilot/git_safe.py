"""Safe argv/env helpers for read-only Git probes."""

from __future__ import annotations

import os


_READ_ONLY_CONFIG = (
    "-c", "core.fsmonitor=false",
    "-c", "core.untrackedCache=false",
    "-c", "core.hooksPath=/dev/null",
)


def argv(root: str, *args: str) -> list[str]:
    return ["git", *_READ_ONLY_CONFIG, "-C", root, *args]


def env() -> dict:
    e = os.environ.copy()
    e["GIT_OPTIONAL_LOCKS"] = "0"
    return e
