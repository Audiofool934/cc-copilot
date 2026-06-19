"""Saved evidence scopes for the cockpit.

These are human-named shortcuts for the read-only evidence scope:
``session``, selected ``multi-session``, or ``project``. They live outside a
single cockpit conversation so a useful project grouping can be reused.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from . import scope as SC, store as ST

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")


@dataclass
class ScopeGroup:
    name: str
    scope: str
    scope_sessions: list[str] = field(default_factory=list)
    updated_at: float = 0.0

    @classmethod
    def from_dict(cls, name: str, data: dict):
        return cls(
            name=name,
            scope=SC.normalize(data.get("scope") or SC.SESSION),
            scope_sessions=list(data.get("scope_sessions") or []),
            updated_at=float(data.get("updated_at") or 0.0),
        )

    def as_dict(self) -> dict:
        return {
            "scope": self.scope,
            "scope_sessions": list(self.scope_sessions or []),
            "updated_at": self.updated_at or time.time(),
        }


def normalize_name(name: str) -> str:
    name = str(name or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError("scope group name must be 1-40 chars: letters, numbers, _, -, .")
    return name


def path() -> str:
    return os.path.join(ST.state_home(), "scope_groups.json")


def _load_raw() -> dict:
    try:
        with open(path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_raw(data: dict) -> None:
    home = ST.state_home()
    os.makedirs(home, exist_ok=True)
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path())


def list_groups() -> list[ScopeGroup]:
    out = []
    for name, data in _load_raw().items():
        if not isinstance(data, dict):
            continue
        try:
            out.append(ScopeGroup.from_dict(name, data))
        except ValueError:
            continue
    out.sort(key=lambda g: g.name.lower())
    return out


def get(name: str) -> Optional[ScopeGroup]:
    key = normalize_name(name)
    data = _load_raw().get(key)
    if not isinstance(data, dict):
        return None
    return ScopeGroup.from_dict(key, data)


def save(name: str, scope: str, scope_sessions=None) -> ScopeGroup:
    key = normalize_name(name)
    group = ScopeGroup(
        name=key,
        scope=SC.normalize(scope),
        scope_sessions=list(scope_sessions or []),
        updated_at=time.time(),
    )
    data = _load_raw()
    data[key] = group.as_dict()
    _write_raw(data)
    return group


def delete(name: str) -> bool:
    key = normalize_name(name)
    data = _load_raw()
    existed = key in data
    if existed:
        del data[key]
        _write_raw(data)
    return existed
