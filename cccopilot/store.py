"""Persistent resumable cockpit sessions (stdlib-only).

cc-copilot is a read-only observer of a Claude Code session; *this* module
persists the SEPARATE Cockpit session: the human/copilot Q&A plus the selected
read target. It never writes under ``~/.claude``.

Layout (under the state home — ``$CC_COPILOT_STATE_DIR`` >
``$XDG_STATE_HOME/cc-copilot`` > ``~/.local/state/cc-copilot``)::

    conversations/<conv_id>/turns.jsonl   append-only, the source of truth
    conversations/<conv_id>/meta.json     derived cache (cheap listing), rebuildable
    conversations/<conv_id>/memory.json   deterministic compacted Q&A memory

Older stores used the observed Claude Code session uuid as ``conv_id``. v0.6
keeps those readable and adds independent Cockpit session ids for new/forked
cockpits. ``turns.jsonl`` holds one self-describing ``{"kind":"head",...}``
line then one ``{"kind":"turn",...}`` line per answered Q&A; readers skip blank
/ unparseable / unknown-``kind`` lines, so a torn final write loses at most that
line and unknown future kinds are ignored.

Concurrency: two cockpits may observe one CC session and thus share one log. A
single ``fcntl.flock(LOCK_EX)`` is held across the WHOLE critical section (append
turn → fsync → re-derive count → atomically rewrite meta → fsync); the turn
count is always re-derived from the log under the lock, so it cannot drift. Where
``fcntl`` is unavailable we fall back to a process-local lock and warn once — we
never pretend a lock-free append is safe (copilot answers routinely exceed any
single-write atomicity bound). Every public write is best-effort: a storage error
degrades to in-memory behavior and never breaks an answer.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

try:
    import fcntl
    _HAVE_FLOCK = True
except ImportError:                                    # pragma: no cover - non-POSIX
    fcntl = None
    _HAVE_FLOCK = False

_PROC_LOCK = threading.Lock()
_WARNED: set = set()


def _warn_once(msg: str) -> None:
    if msg not in _WARNED:
        _WARNED.add(msg)
        print(msg, file=sys.stderr)


# ── locations & toggles ─────────────────────────────────────────────────────
def state_home() -> str:
    """Where persistent state lives. Env wins, then XDG, then ~/.local/state."""
    d = os.environ.get("CC_COPILOT_STATE_DIR")
    if d:
        return os.path.expanduser(d)
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return os.path.join(xdg, "cc-copilot")
    return os.path.expanduser("~/.local/state/cc-copilot")


def _conv_root() -> str:
    return os.path.join(state_home(), "conversations")


def enabled() -> bool:
    """Master opt-out. Delegates to config so it owns all TOML parsing."""
    try:
        from . import config
        return config.history_enabled()
    except Exception:
        return True


def conv_id_for(path: str, tr=None) -> str:
    """Stable id for a conversation tied to an observed CC session.

    Prefer the parsed session uuid, else the transcript filename (a uuid), else
    a sha1 of the absolute path so any path still maps somewhere safe.
    """
    sid = getattr(tr, "session_id", "") if tr is not None else ""
    if sid:
        return _safe(sid)
    if path:
        base = os.path.basename(path)
        if base.endswith(".jsonl"):
            base = base[:-6]
        if base:
            return _safe(base)
        return "sha1-" + hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:16]
    return "unknown"


def _safe(name: str) -> str:
    out = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in name)[:128]
    # never let a name resolve to a reserved path component — '.'/'..' would
    # escape conversations/<id>/ (a '...jsonl' transcript yields '..').
    return out if out not in ("", ".", "..") else "unknown"


# ── conversation header (cheap listing row) ─────────────────────────────────
@dataclass
class ConvHeader:
    conv_id: str
    session_id: str = ""
    cwd: str = ""
    transcript: str = ""
    title: str = "(untitled)"
    created: float = 0.0
    updated: float = 0.0
    turns: int = 0
    last_q: str = ""
    last_a_head: str = ""
    backend: str = ""
    model: str = ""
    transcript_present: bool = False
    sources: list = field(default_factory=list)
    scope: str = "session"
    scope_sessions: list = field(default_factory=list)

    @classmethod
    def from_meta(cls, d: dict) -> "ConvHeader":
        return cls(
            conv_id=d.get("conv_id", ""), session_id=d.get("session_id", ""),
            cwd=d.get("cwd", ""), transcript=d.get("transcript", "") or "",
            title=d.get("title") or "(untitled)",
            created=float(d.get("created", 0) or 0), updated=float(d.get("updated", 0) or 0),
            turns=int(d.get("turns", 0) or 0),
            last_q=d.get("last_q", ""), last_a_head=d.get("last_a_head", ""),
            backend=d.get("backend") or "", model=d.get("model") or "",
            transcript_present=bool(d.get("transcript_present", False)),
            sources=list(d.get("sources") or []),
            scope=d.get("scope") or "session",
            scope_sessions=list(d.get("scope_sessions") or []),
        )

    def ago(self) -> str:
        from . import locate
        return locate.ago(self.updated)


# ── the store ───────────────────────────────────────────────────────────────
class Store:
    def __init__(self, conv_id: str, enabled: bool = True):
        self.conv_id = conv_id
        self.enabled = bool(enabled)
        self.transcript = ""
        self.session_id = ""
        self.cwd = ""
        self.title = ""
        self.scope = "session"
        self.scope_sessions = []

    @classmethod
    def open_for(cls, path, enabled: bool = True, tr=None) -> "Store":
        s = cls(conv_id_for(path, tr), enabled=enabled)
        s.transcript = os.path.abspath(path) if path else ""
        if tr is not None:
            s.session_id = getattr(tr, "session_id", "") or ""
            s.cwd = getattr(tr, "cwd", "") or ""
            s.title = getattr(tr, "title", "") or ""
        return s

    @classmethod
    def new_for(cls, path, enabled: bool = True, tr=None) -> "Store":
        s = cls("cockpit-" + uuid.uuid4().hex[:16], enabled=enabled)
        s.transcript = os.path.abspath(path) if path else ""
        if tr is not None:
            s.session_id = getattr(tr, "session_id", "") or ""
            s.cwd = getattr(tr, "cwd", "") or ""
            s.title = getattr(tr, "title", "") or ""
        return s

    # paths
    @property
    def dir(self) -> str:
        return os.path.join(_conv_root(), self.conv_id)

    @property
    def turns_path(self) -> str:
        return os.path.join(self.dir, "turns.jsonl")

    @property
    def meta_path(self) -> str:
        return os.path.join(self.dir, "meta.json")

    @property
    def memory_path(self) -> str:
        return os.path.join(self.dir, "memory.json")

    @property
    def lock_path(self) -> str:
        # A stable per-conversation lock file. We hold the exclusive lock on
        # THIS (never replaced) rather than on turns.jsonl, so truncate()'s
        # atomic os.replace of the log cannot orphan the lock and silently drop
        # a concurrent append — flock is per-inode, and replace swaps the inode.
        return os.path.join(self.dir, ".lock")

    @contextlib.contextmanager
    def _dir_locked(self):
        """Hold this conversation's exclusive lock across a whole write critical
        section. Every mutator (append / truncate / state) takes it, so they
        serialize across threads AND processes (two cockpits on one session),
        and across the inode swap truncate() performs on turns.jsonl."""
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as lock_fh:
            with _locked(lock_fh):
                yield

    # ---- write (best-effort) ----
    def record_turn(self, q: str, a: str, st=None, backend=None, model=None,
                    usage=None) -> bool:
        if not self.enabled:
            return False
        try:
            self._refresh_from(st)
            self._write(q, a, st, backend, model, usage)
            return True
        except Exception as e:        # best-effort: a storage error never breaks an answer
            _warn_once(f"cc-copilot: history write failed ({e}); continuing in-memory only")
            return False

    def record_state(self, st=None, scope=None, scope_sessions=None,
                     backend=None, model=None) -> bool:
        """Persist cockpit-session metadata even before any Q&A turn exists."""
        if not self.enabled:
            return False
        try:
            self._refresh_from(st)
            if scope:
                self.scope = scope
            if scope_sessions is not None:
                self.scope_sessions = list(scope_sessions or [])
            with self._dir_locked():
                _chmod(self.dir, 0o700)
                _chmod(_conv_root(), 0o700)
                _chmod(state_home(), 0o700)
                prev = _read_json(self.meta_path) or {}
                # Re-derive the count from the LOG under the lock — writing back
                # a stale meta count could stomp a concurrent turn's increment.
                self._write_meta(self._count_turns(),
                                 prev.get("last_q", ""), prev.get("last_a_head", ""),
                                 backend or prev.get("backend"),
                                 model or prev.get("model"))
            return True
        except Exception as e:
            _warn_once(f"cc-copilot: cockpit state write failed ({e}); continuing in-memory only")
            return False

    def _refresh_from(self, st):
        tr = getattr(st, "tr", None)
        if tr is not None:
            self.session_id = self.session_id or (getattr(tr, "session_id", "") or "")
            self.cwd = self.cwd or (getattr(tr, "cwd", "") or "")
            self.title = (getattr(tr, "title", "") or "") or self.title

    def _write(self, q, a, st, backend, model, usage=None):
        with self._dir_locked():
            _chmod(self.dir, 0o700)
            _chmod(_conv_root(), 0o700)
            _chmod(state_home(), 0o700)
            fd = os.open(self.turns_path, os.O_RDWR | os.O_CREAT, 0o600)
            # errors="replace" so a torn (partial multibyte) final line from a
            # prior crash can't make our own read raise; newline="" so
            # endswith("\n") is exact.
            with os.fdopen(fd, "r+", encoding="utf-8", errors="replace", newline="") as fh:
                fh.seek(0)
                existing = fh.read()
                prefix = ""
                if not existing:
                    prefix = json.dumps(self._head(), ensure_ascii=False) + "\n"
                elif not existing.endswith("\n"):
                    prefix = "\n"          # close a torn final line so the new turn
                                           # is its own parseable line, not glued on
                fh.seek(0, os.SEEK_END)
                fh.write(prefix + json.dumps(self._turn(q, a, st, backend, model, usage),
                                             ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                _fsync_dir(self.dir)       # durably link a brand-new conversation log
                n = self._count_turns_locked(fh)
                self._write_meta(n, q, a, backend, model)

    def _count_turns_locked(self, fh) -> int:
        fh.seek(0)
        n = 0
        for line in fh:
            obj = _parse(line)
            if obj is not None and obj.get("kind") == "turn":
                n += 1
        return n

    def _count_turns(self) -> int:
        """Turn count read straight from the log (0 if absent). Call under
        _dir_locked so it reflects committed state, never a stale meta cache."""
        try:
            with open(self.turns_path, "r", encoding="utf-8", errors="replace") as fh:
                return self._count_turns_locked(fh)
        except OSError:
            return 0

    def _head(self) -> dict:
        return {"kind": "head", "schema": 2, "conv_id": self.conv_id,
                "session_id": self.session_id, "cwd": self.cwd,
                "transcript": self.transcript, "created": time.time(),
                "scope": self.scope, "scope_sessions": list(self.scope_sessions or [])}

    def _turn(self, q, a, st, backend, model, usage=None) -> dict:
        tr = getattr(st, "tr", None)
        rec = {
            "kind": "turn", "id": "%020d" % time.time_ns(), "ts": time.time(),
            "q": q, "a": a, "backend": backend, "model": model,
            "obs": "%s:%d" % (_host(), os.getpid()),
            "src": {
                "session_id": (getattr(tr, "session_id", "") if tr else "") or self.session_id,
                "cwd": (getattr(tr, "cwd", "") if tr else "") or self.cwd,
                "transcript": self.transcript,
                "tr_lines": getattr(tr, "raw_lines", 0) if tr else 0,
                "status": getattr(st, "status", "") if st is not None else "",
                "scope": self.scope,
                "scope_sessions": list(self.scope_sessions or []),
            },
        }
        if usage:
            rec["usage"] = dict(usage)
        return rec

    def _write_meta(self, n, q, a, backend, model):
        prev = _read_json(self.meta_path) or {}
        now = time.time()
        transcript = self.transcript or prev.get("transcript", "")
        prev_title = prev.get("title") or ""
        title = self.title or (prev_title if prev_title != "(untitled)" else "") or _title_from(q)
        meta = {
            "schema": 2, "conv_id": self.conv_id,
            "session_id": self.session_id or prev.get("session_id", ""),
            "cwd": self.cwd or prev.get("cwd", ""),
            "transcript": transcript, "title": title,
            "created": prev.get("created", now), "updated": now, "turns": n,
            "last_q": q[:200], "last_a_head": a[:200],
            "backend": backend or prev.get("backend"),
            "model": model or prev.get("model"),
            "transcript_present": bool(transcript and os.path.isfile(transcript)),
            "sources": prev.get("sources") or ([self.session_id] if self.session_id else []),
            "scope": self.scope or prev.get("scope") or "session",
            "scope_sessions": list(self.scope_sessions if self.scope_sessions is not None
                                   else prev.get("scope_sessions") or []),
        }
        tmp = self.meta_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as mf:
            json.dump(meta, mf, ensure_ascii=False)
            mf.flush()
            os.fsync(mf.fileno())
        os.replace(tmp, self.meta_path)
        _fsync_dir(self.dir)               # persist the rename

    # ---- read (no lock; tolerant) ----
    def load_history(self) -> list:
        """Return [(role, text), …] — two entries per stored turn. Honors the
        opt-out: a disabled store reads nothing back (in-memory only, no replay
        of prior plaintext into prompts)."""
        if not self.enabled:
            return []
        out = []
        try:
            with open(self.turns_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    obj = _parse(line)
                    if obj is None or obj.get("kind") != "turn":
                        continue
                    out.append(("user", obj.get("q", "")))
                    out.append(("assistant", obj.get("a", "")))
        except OSError:
            return []
        return out

    def load_turn_times(self) -> list:
        """Per-turn local ``HH:MM`` strings, aligned 1:1 with :meth:`load_history`
        (two identical entries per turn — the same turn time for the user row and
        its answer row). Used only for restored-history display; empty when the
        store is disabled or unreadable, and entries are ``""`` when a turn has no
        recorded ``ts`` so callers simply show no time."""
        if not self.enabled:
            return []
        out = []
        try:
            with open(self.turns_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    obj = _parse(line)
                    if obj is None or obj.get("kind") != "turn":
                        continue
                    hhmm = _hhmm_local(obj.get("ts"))
                    out.append(hhmm)
                    out.append(hhmm)
        except OSError:
            return []
        return out

    def load_memory(self) -> str:
        """Durable structured memory for older cockpit turns, if present."""
        if not self.enabled:
            return ""
        d = _read_json(self.memory_path)
        if not isinstance(d, dict):
            return ""
        return d.get("text", "") if isinstance(d.get("text"), str) else ""

    def compact_memory(self, history=None, max_raw_chars: int = 36000) -> tuple:
        """Return ``(memory_text, recent_history)`` for a budgeted prompt.

        The raw ``turns.jsonl`` log remains complete. This writes only a
        rebuildable ``memory.json`` sidecar derived from older turns.
        """
        if not self.enabled:
            return "", list(history or [])
        turns = _history_to_turns(history) if history is not None else self._load_turns()
        if not turns:
            self._delete_memory()
            return "", []
        recent = _recent_turns_by_budget(turns, max_raw_chars)
        older = turns[:max(0, len(turns) - len(recent))]
        recent_history = _turns_to_history(recent)
        if not older:
            self._delete_memory()
            return "", recent_history
        text = _memory_text(older, kept_recent=len(recent))
        try:
            self._write_memory(text, len(older), len(recent))
        except Exception as e:
            _warn_once(f"cc-copilot: memory compaction failed ({e}); continuing with raw history")
            return "", list(history or _turns_to_history(turns))
        return text, recent_history

    def delete(self) -> bool:
        """Remove this conversation's saved files (best-effort). conv_id is
        sanitized, so this can only ever touch conversations/<conv_id>/."""
        import shutil
        try:
            shutil.rmtree(self.dir)
            return True
        except OSError:
            return False

    def truncate(self, n: int) -> bool:
        """Keep only the first ``n`` turns (drop the rest) — for rewind/fork.
        Rewrites the log atomically under the lock and refreshes the meta cache."""
        if not self.enabled or not os.path.isfile(self.turns_path):
            return False
        try:
            with self._dir_locked():
                fd = os.open(self.turns_path, os.O_RDWR, 0o600)
                with os.fdopen(fd, "r+", encoding="utf-8", errors="replace", newline="") as fh:
                    fh.seek(0)
                    head_line, turns = None, []
                    for line in fh:
                        obj = _parse(line)
                        if obj is None:
                            continue
                        nl = line if line.endswith("\n") else line + "\n"
                        if obj.get("kind") == "head" and head_line is None:
                            head_line = nl
                        elif obj.get("kind") == "turn":
                            turns.append((nl, obj))
                    kept = turns[:max(0, n)]
                    tmp = self.turns_path + ".tmp"
                    tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(tfd, "w", encoding="utf-8", newline="") as tf:
                        if head_line:
                            tf.write(head_line)
                        tf.writelines(nl for nl, _ in kept)
                        tf.flush()
                        os.fsync(tf.fileno())
                    # Safe under _dir_locked: the lock is on the stable .lock
                    # file, not on this inode that replace is about to swap.
                    os.replace(tmp, self.turns_path)
                    _fsync_dir(self.dir)
                    last = kept[-1][1] if kept else {}
                    self._write_meta(len(kept), last.get("q", ""), last.get("a", ""),
                                     last.get("backend"), last.get("model"))
                    self._delete_memory()
            return True
        except (OSError, ValueError):
            return False

    def header(self) -> "ConvHeader | None":
        d = _read_json(self.meta_path)
        if d is None:
            d = _rebuild_meta_dict(self.conv_id)
        if not d:
            return None
        h = ConvHeader.from_meta(d)
        h.transcript_present = bool(h.transcript and os.path.isfile(h.transcript))
        return h

    def apply_header(self, h: "ConvHeader") -> None:
        self.transcript = h.transcript or self.transcript
        self.session_id = h.session_id or self.session_id
        self.cwd = h.cwd or self.cwd
        self.title = h.title or self.title
        self.scope = h.scope or self.scope
        self.scope_sessions = list(h.scope_sessions or [])

    def _load_turns(self) -> list:
        if not self.enabled:
            return []
        turns = []
        try:
            with open(self.turns_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    obj = _parse(line)
                    if obj is not None and obj.get("kind") == "turn":
                        turns.append(obj)
        except OSError:
            return []
        return turns

    def _write_memory(self, text: str, source_turns: int, recent_turns: int) -> None:
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        _chmod(self.dir, 0o700)
        payload = {
            "schema": 1,
            "updated": time.time(),
            "source_turns": int(source_turns),
            "recent_turns": int(recent_turns),
            "text": text,
        }
        tmp = self.memory_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as mf:
            json.dump(payload, mf, ensure_ascii=False)
            mf.flush()
            os.fsync(mf.fileno())
        os.replace(tmp, self.memory_path)
        _fsync_dir(self.dir)

    def _delete_memory(self) -> None:
        try:
            os.unlink(self.memory_path)
            _fsync_dir(self.dir)
        except OSError:
            pass


# ── module-level helpers ────────────────────────────────────────────────────
@contextlib.contextmanager
def _locked(fh):
    if _HAVE_FLOCK:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            # locking unsupported at runtime (e.g. NFS without lockd → ENOLCK).
            # degrade to single-writer mode rather than killing persistence —
            # still never a lock-free concurrent append.
            _warn_once("cc-copilot: file locking unavailable here (e.g. NFS without "
                       "lockd) — history in single-writer mode; concurrent cockpits "
                       "on one session are unprotected")
            with _PROC_LOCK:
                yield
            return
        try:
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    else:                                                  # pragma: no cover - non-POSIX
        _warn_once("cc-copilot: fcntl unavailable — history runs in single-writer "
                   "mode (concurrent cockpits on one session are unprotected)")
        with _PROC_LOCK:
            yield


def _chmod(path, mode):
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _fsync_dir(path):
    """Best-effort fsync of a directory so a freshly-linked file survives a crash."""
    try:
        dfd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:                                        # not supported on this OS/fs
        pass


def _host() -> str:
    try:
        import socket
        return socket.gethostname() or "host"
    except Exception:
        return "host"


def _hhmm_local(ts) -> str:
    """A stored epoch ``ts`` → local ``HH:MM``; ``""`` when missing/unparseable.
    A corrupt/out-of-range ``ts`` (e.g. ``1e9999`` → ``inf`` from a hand-edited
    state file) makes ``localtime`` raise OverflowError/OSError — caught here so a
    bad turn record never crashes history restore, it just shows no time."""
    try:
        return time.strftime("%H:%M", time.localtime(float(ts))) if ts else ""
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _parse(line):
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    # Callers (header/from_meta, list_conversations) index this like a dict; an
    # externally-edited or legacy non-object meta.json must not crash them.
    return d if isinstance(d, dict) else None


def _title_from(q: str) -> str:
    line = (q or "").strip().splitlines()[0] if (q or "").strip() else ""
    line = line[:60].strip()
    return line or "(untitled)"


def _history_to_turns(history) -> list:
    turns, pending = [], None
    for role, text in list(history or []):
        if role == "user":
            pending = {"q": str(text or ""), "a": ""}
        elif role == "assistant":
            if pending is None:
                pending = {"q": "", "a": str(text or "")}
            else:
                pending["a"] = str(text or "")
            turns.append(pending)
            pending = None
    if pending is not None:
        turns.append(pending)
    return turns


def _turns_to_history(turns: list) -> list:
    out = []
    for t in turns:
        out.append(("user", t.get("q", "")))
        out.append(("assistant", t.get("a", "")))
    return out


def _turn_cost(t: dict) -> int:
    return len(t.get("q", "")) + len(t.get("a", "")) + 80


def _recent_turns_by_budget(turns: list, max_raw_chars: int) -> list:
    if not turns:
        return []
    max_raw_chars = max(1, int(max_raw_chars or 0))
    recent, used = [], 0
    for t in reversed(turns):
        cost = _turn_cost(t)
        if recent and used + cost > max_raw_chars:
            break
        recent.append(t)
        used += cost
    recent.reverse()
    return recent


_CITE_RE = re.compile(r"\[(?:[A-Za-z0-9_.-]+:)?L\d+[^\]]*\]")


def _memory_text(turns: list, kept_recent: int = 0) -> str:
    rows = [
        f"- Deterministic compaction of {len(turns)} older cockpit turn(s); "
        "the complete raw Q&A log remains in `turns.jsonl`.",
        f"- Recent raw turn(s) kept outside memory: {kept_recent}.",
        "",
    ]
    sections = [
        ("Decisions Made", _memory_decisions(turns)),
        ("User Preferences", _memory_preferences(turns)),
        ("Known Project Facts Discussed", _memory_facts(turns)),
        ("Open Questions", _memory_questions(turns)),
        ("Discarded Assumptions", _memory_discarded(turns)),
        ("Important Citations", _memory_citations(turns)),
    ]
    for title, lines in sections:
        rows.append(f"### {title}")
        rows.extend(lines or ["- (none captured deterministically)"])
        rows.append("")
    return "\n".join(rows).rstrip()


def _memory_decisions(turns: list) -> list:
    keys = ("ok", "okay", "yes", "ship", "commit", "merge", "decide", "decision",
            "let's", "lets", "use ", "scope", "enable", "disable", "deprecate")
    return _memory_matches(turns, keys, prefer="q", limit=8)


def _memory_preferences(turns: list) -> list:
    keys = ("i think", "i want", "i believe", "prefer", "should", "need",
            "better", "no need", "default", "always")
    return _memory_matches(turns, keys, prefer="q", limit=8)


def _memory_facts(turns: list) -> list:
    out = []
    for i, t in enumerate(turns, 1):
        a = t.get("a", "")
        cites = _CITE_RE.findall(a)
        if cites:
            out.append(f"- Turn {i}: {_clip(_first_sentence(a), 220)} {' '.join(cites[:4])}")
        if len(out) >= 8:
            break
    return out


def _memory_questions(turns: list) -> list:
    out = []
    for i, t in enumerate(turns, 1):
        q = t.get("q", "")
        if "?" in q or any(k in q.lower() for k in ("what", "why", "how", "whether", "吗")):
            out.append(f"- Turn {i}: {_clip(q, 220)}")
        if len(out) >= 8:
            break
    return out


def _memory_discarded(turns: list) -> list:
    keys = ("not ", "don't", "do not", "instead", "rather", "remove", "deprecate",
            "no need", "shouldn't", "avoid")
    return _memory_matches(turns, keys, prefer="qa", limit=8)


def _memory_citations(turns: list) -> list:
    seen, out = set(), []
    for i, t in enumerate(turns, 1):
        cites = _CITE_RE.findall((t.get("q", "") + "\n" + t.get("a", "")))
        unique = []
        for cite in cites:
            if cite not in seen:
                seen.add(cite)
                unique.append(cite)
        if unique:
            out.append(f"- Turn {i}: {' '.join(unique[:8])}")
        if len(out) >= 10:
            break
    return out


def _memory_matches(turns: list, keys: tuple, prefer: str, limit: int) -> list:
    out = []
    for i, t in enumerate(turns, 1):
        q, a = t.get("q", ""), t.get("a", "")
        hay = (q + "\n" + a).lower() if prefer == "qa" else q.lower()
        if any(k in hay for k in keys):
            text = q if prefer in ("q", "qa") and q.strip() else a
            out.append(f"- Turn {i}: {_clip(text, 220)}")
        if len(out) >= limit:
            break
    return out


def _first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    parts = re.split(r"(?<=[.!?。！？])\s+", text, maxsplit=1)
    return parts[0] if parts else text


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _rebuild_meta_dict(conv_id: str):
    """Reconstruct a meta dict from the log alone (self-heal a lost/corrupt cache)."""
    turns_path = os.path.join(_conv_root(), conv_id, "turns.jsonl")
    head, first, last, n, created = {}, {}, {}, 0, 0.0
    try:
        with open(turns_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                obj = _parse(line)
                if obj is None:
                    continue
                if obj.get("kind") == "head":
                    head = obj
                    created = float(obj.get("created", 0) or 0)
                elif obj.get("kind") == "turn":
                    n += 1
                    if not first:
                        first = obj
                    last = obj
    except OSError:
        return None
    if not head and not last:
        return None
    src = last.get("src", {}) if last else {}
    transcript = head.get("transcript") or src.get("transcript", "")
    d = {
        "schema": 1, "conv_id": conv_id,
        "session_id": head.get("session_id") or src.get("session_id", ""),
        "cwd": head.get("cwd") or src.get("cwd", ""),
        "transcript": transcript,
        "title": _title_from(first.get("q", "")) if first else "(untitled)",
        "created": created or last.get("ts", 0.0), "updated": last.get("ts", 0.0),
        "turns": n, "last_q": (last.get("q", "") or "")[:200],
        "last_a_head": (last.get("a", "") or "")[:200],
        "backend": last.get("backend"), "model": last.get("model"),
        "transcript_present": bool(transcript and os.path.isfile(transcript)),
        "sources": [head.get("session_id")] if head.get("session_id") else [],
        "scope": src.get("scope") or "session",
        "scope_sessions": list(src.get("scope_sessions") or []),
    }
    return d


def list_conversations(cwd=None) -> list:
    """Newest-first conversation headers, optionally filtered to one project cwd.

    Reads only the tiny per-conversation meta.json (never a turns log), so it is
    O(#conversations). A missing/corrupt meta self-heals from its own log.
    """
    out = []
    try:
        names = os.listdir(_conv_root())
    except OSError:
        return []
    target = os.path.abspath(cwd) if cwd else None
    for cid in names:
        meta_p = os.path.join(_conv_root(), cid, "meta.json")
        d = _read_json(meta_p) or _rebuild_meta_dict(cid)
        if not d:
            continue
        h = ConvHeader.from_meta(d)
        h.transcript_present = bool(h.transcript and os.path.isfile(h.transcript))
        if target is not None and os.path.abspath(h.cwd or "") != target:
            continue
        out.append(h)
    out.sort(key=lambda h: h.updated, reverse=True)
    return out
