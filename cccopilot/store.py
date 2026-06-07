"""Persistent copilot history (stdlib-only).

cc-copilot is a read-only observer of a Claude Code session; *this* module
persists the SEPARATE thing the human and the copilot say to each other ABOUT
that session, so switching sessions in the cockpit — or relaunching entirely —
restores the prior dialogue instead of losing it. It never writes under
``~/.claude``.

Layout (under the state home — ``$CC_COPILOT_STATE_DIR`` >
``$XDG_STATE_HOME/cc-copilot`` > ``~/.local/state/cc-copilot``)::

    conversations/<conv_id>/turns.jsonl   append-only, the source of truth
    conversations/<conv_id>/meta.json     derived cache (cheap listing), rebuildable

``conv_id`` is the observed Claude Code session uuid, so re-attaching to the same
agent restores the same conversation. ``turns.jsonl`` holds one self-describing
``{"kind":"head",...}`` line then one ``{"kind":"turn",...}`` line per answered
Q&A; readers skip blank / unparseable / unknown-``kind`` lines, so a torn final
write loses at most that line and unknown future kinds are ignored.

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
import sys
import threading
import time
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

    @classmethod
    def open_for(cls, path, enabled: bool = True, tr=None) -> "Store":
        s = cls(conv_id_for(path, tr), enabled=enabled)
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

    # ---- write (best-effort) ----
    def record_turn(self, q: str, a: str, st=None, backend=None, model=None) -> bool:
        if not self.enabled:
            return False
        try:
            self._refresh_from(st)
            self._write(q, a, st, backend, model)
            return True
        except Exception as e:        # best-effort: a storage error never breaks an answer
            _warn_once(f"cc-copilot: history write failed ({e}); continuing in-memory only")
            return False

    def _refresh_from(self, st):
        tr = getattr(st, "tr", None)
        if tr is not None:
            self.session_id = self.session_id or (getattr(tr, "session_id", "") or "")
            self.cwd = self.cwd or (getattr(tr, "cwd", "") or "")
            self.title = (getattr(tr, "title", "") or "") or self.title

    def _write(self, q, a, st, backend, model):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        _chmod(self.dir, 0o700)
        _chmod(_conv_root(), 0o700)
        _chmod(state_home(), 0o700)
        fd = os.open(self.turns_path, os.O_RDWR | os.O_CREAT, 0o600)
        # errors="replace" so a torn (partial multibyte) final line from a prior
        # crash can't make our own read raise; newline="" so endswith("\n") is exact.
        with os.fdopen(fd, "r+", encoding="utf-8", errors="replace", newline="") as fh:
            with _locked(fh):
                fh.seek(0)
                existing = fh.read()
                prefix = ""
                if not existing:
                    prefix = json.dumps(self._head(), ensure_ascii=False) + "\n"
                elif not existing.endswith("\n"):
                    prefix = "\n"          # close a torn final line so the new turn
                                           # is its own parseable line, not glued on
                fh.seek(0, os.SEEK_END)
                fh.write(prefix + json.dumps(self._turn(q, a, st, backend, model),
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

    def _head(self) -> dict:
        return {"kind": "head", "schema": 1, "conv_id": self.conv_id,
                "session_id": self.session_id, "cwd": self.cwd,
                "transcript": self.transcript, "created": time.time()}

    def _turn(self, q, a, st, backend, model) -> dict:
        tr = getattr(st, "tr", None)
        return {
            "kind": "turn", "id": "%020d" % time.time_ns(), "ts": time.time(),
            "q": q, "a": a, "backend": backend, "model": model,
            "obs": "%s:%d" % (_host(), os.getpid()),
            "src": {
                "session_id": (getattr(tr, "session_id", "") if tr else "") or self.session_id,
                "cwd": (getattr(tr, "cwd", "") if tr else "") or self.cwd,
                "transcript": self.transcript,
                "tr_lines": getattr(tr, "raw_lines", 0) if tr else 0,
                "status": getattr(st, "status", "") if st is not None else "",
            },
        }

    def _write_meta(self, n, q, a, backend, model):
        prev = _read_json(self.meta_path) or {}
        now = time.time()
        transcript = self.transcript or prev.get("transcript", "")
        title = self.title or prev.get("title") or _title_from(q)
        meta = {
            "schema": 1, "conv_id": self.conv_id,
            "session_id": self.session_id or prev.get("session_id", ""),
            "cwd": self.cwd or prev.get("cwd", ""),
            "transcript": transcript, "title": title,
            "created": prev.get("created", now), "updated": now, "turns": n,
            "last_q": q[:200], "last_a_head": a[:200],
            "backend": backend or prev.get("backend"),
            "model": model or prev.get("model"),
            "transcript_present": bool(transcript and os.path.isfile(transcript)),
            "sources": prev.get("sources") or ([self.session_id] if self.session_id else []),
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
            fd = os.open(self.turns_path, os.O_RDWR, 0o600)
            with os.fdopen(fd, "r+", encoding="utf-8", errors="replace", newline="") as fh:
                with _locked(fh):
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
                    os.replace(tmp, self.turns_path)
                    _fsync_dir(self.dir)
                    last = kept[-1][1] if kept else {}
                    self._write_meta(len(kept), last.get("q", ""), last.get("a", ""),
                                     last.get("backend"), last.get("model"))
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
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _title_from(q: str) -> str:
    line = (q or "").strip().splitlines()[0] if (q or "").strip() else ""
    line = line[:60].strip()
    return line or "(untitled)"


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
