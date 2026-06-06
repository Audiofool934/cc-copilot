"""The persistent, parallel, read-only chat sidecar (`cc-copilot chat`).

Pin to one *other* session and hold an ongoing QA conversation while the copilot
shares window-1's LIVE timeline: every turn re-parses the (growing) JSONL, so an
answer can never lag the agent. A background thread watches the file and pushes
inline "it just stalled / went off-track" alerts so it feels parallel, not
pull-only.

Read-only by construction: the only filesystem operation anywhere in the path is
``open(path, 'r')``. There is no handle to window-1's process and no write verb
to add — it cannot affect the agent it watches.
"""

from __future__ import annotations

import os
import sys
import threading

from . import transcript as T, state as S, brief as B, assess as A, narrate as N, locate as LOC

_GLYPH = {"running": "🟢", "stalled": "🔴", "awaiting-agent": "🟡",
          "idle": "⚪", "empty": "∅"}

_HELP = """commands (all but questions are LLM-free):
  /brief            full evidence-cited recap
  /check            safety verdict + friction signals
  /diff             what changed since your last turn
  /refresh          re-read the session now
  /session          which session is attached
  /sessions         list other sessions in this project
  /use <n|id>       switch to another session (clears chat context)
  /history          this chat's turns
  /help             this
  /exit  /quit      leave  (Ctrl-D also works)
anything else → a question answered grounded in the live session state."""


def _dur(sec):
    if sec is None:
        return "?"
    s = int(sec)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600}h"


def _fmt_diff(d) -> str:
    if (d.new_events == 0 and not d.new_failures and not d.new_changed
            and d.status_from == d.status_to):
        return "(no change since last turn)"
    L = [f"since last turn: +{d.new_events} events"]
    if d.status_from != d.status_to:
        L.append(f"  status: {d.status_from or '∅'} → {d.status_to}")
    if d.verdict_from != d.verdict_to:
        L.append(f"  safety: {d.verdict_from or '∅'} → {d.verdict_to}")
    for fc in d.new_changed[:6]:
        L.append(f"  ~ {fc.path} ({fc.total} edit/write) [L{fc.last_line}]")
    for f in d.new_failures[:5]:
        L.append(f"  ⚠ {f.tool} failed [L{f.line}]: {f.summary[:80]}")
    return "\n".join(L)


def _fmt_alert(d) -> str:
    """An alert only fires on a meaningful *transition*, to stay quiet."""
    bits = []
    if d.status_to != d.status_from and d.status_to in ("stalled", "awaiting-agent"):
        bits.append(f"window-1 → {d.status_to.upper()}")
    if d.verdict_to == "intervene" and d.verdict_from != "intervene":
        bits.append("safety → INTERVENE")
    if d.new_failures:
        f = d.new_failures[-1]
        bits.append(f"{len(d.new_failures)} new error(s), e.g. {f.tool} [L{f.line}]")
    if not bits:
        return ""
    tail = f"  (+{d.new_events} events)" if d.new_events else ""
    return " · ".join(bits) + tail


class ChatSession:
    def __init__(self, path, model=None, backend=None, alerts=True, poll=5):
        self.path = path
        self.model = model
        self.backend = backend
        self.poll = max(2, poll)
        self.history = []          # [(role, text)]
        self.st = None
        self.prev = None
        self.last_size = -1
        self._alerts = alerts
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._alert_size = -1
        self._alert_state = None
        self._thread = None

    # ---- live state ------------------------------------------------------
    def refresh(self) -> bool:
        """Re-read the session if it grew. Returns True if it changed."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = -1
        if size == self.last_size and self.st is not None:
            return False
        self.last_size = size
        self.prev = self.st
        self.st = S.build(T.parse(self.path))
        return True

    def banner(self) -> str:
        st = self.st
        return (f"[{_GLYPH.get(st.status, '?')} {st.status} · idle "
                f"{_dur(st.idle_seconds)} · {st.tr.raw_lines} ev · "
                f"safety: {A.assess(st).verdict}]")

    def answer(self, q: str) -> str:
        self.refresh()
        txt = N.chat(self.st, self.history, q, model=self.model, backend=self.backend)
        self.history.append(("user", q))
        self.history.append(("assistant", txt))
        return txt

    def meta(self, cmd: str):
        """Handle a /command. Returns text to print, or False to exit."""
        c = cmd.strip().lower()
        if c in ("/exit", "/quit"):
            return False
        self.refresh()
        if c == "/help":
            return _HELP
        if c == "/brief":
            return B.render(self.st)
        if c == "/check":
            return B.render_check(self.st)
        if c == "/refresh":
            return self.banner() + "  (refreshed)"
        if c == "/session":
            return f"attached: {self.path}\n{self.banner()}"
        if c == "/sessions":
            return self._list_sessions()
        if c.startswith("/use"):
            return self._switch(cmd.strip()[4:].strip())
        if c == "/history":
            if not self.history:
                return "(no turns yet)"
            return "\n".join(("you> " if r == "user" else "cc > ") + t[:200]
                             for r, t in self.history)
        if c == "/diff":
            return _fmt_diff(S.diff(self.prev, self.st))
        return f"unknown command {cmd!r} — try /help"

    # ---- session switching (select among multiple sessions) --------------
    def _siblings(self):
        d = os.path.dirname(self.path)
        refs = []
        for n in os.listdir(d):
            if n.endswith(".jsonl"):
                p = os.path.join(d, n)
                # hide cc-copilot's own narration sessions, but never hide the
                # one we're currently attached to.
                if p != self.path and LOC.is_own_session(p):
                    continue
                try:
                    refs.append((os.path.getmtime(p), p))
                except OSError:
                    pass
        refs.sort(reverse=True)
        self._listing = [p for _, p in refs]
        return self._listing

    def _list_sessions(self):
        paths = self._siblings()
        out = ["sessions in this project (newest first — `/use <n|id>`):"]
        for i, p in enumerate(paths, 1):
            cur = "*" if os.path.samefile(p, self.path) else " "
            sid = os.path.basename(p)[:-6]
            try:
                kb = os.path.getsize(p) // 1024
            except OSError:
                kb = 0
            out.append(f" {cur}{i:>2}. {sid[:8]}  {kb:>6} KB")
        return "\n".join(out)

    def _switch(self, arg):
        if not arg:
            return "usage: /use <number|session-id|prefix>  (see /sessions)"
        paths = getattr(self, "_listing", None) or self._siblings()
        target = None
        if arg.isdigit():
            i = int(arg) - 1
            if 0 <= i < len(paths):
                target = paths[i]
        if target is None:
            for p in paths:
                sid = os.path.basename(p)[:-6]
                if sid == arg or sid.startswith(arg):
                    target = p
                    break
        if target is None:
            return f"no session matching {arg!r} — try /sessions"
        if os.path.samefile(target, self.path):
            return "already attached to that session"
        self.switch_path(target)
        return (f"switched → {os.path.basename(target)[:-6][:8]} "
                f"(chat context cleared)\n{self.banner()}")

    def switch_path(self, path):
        """Re-pin to another session: fresh state + fresh conversation context."""
        self.path = path
        self.st = self.prev = None
        self.last_size = -1
        self.history = []
        self._alert_state = None
        self._alert_size = -1
        self.refresh()

    def siblings(self):
        """Public list of sibling session paths (newest first), own-filtered."""
        return self._siblings()

    # ---- background alerts (read-only, advisory) -------------------------
    def _start_alerts(self):
        if not self._alerts:
            return
        self._alert_size = self.last_size
        self._alert_state = self.st
        self._thread = threading.Thread(target=self._alert_loop, daemon=True)
        self._thread.start()

    def _alert_loop(self):
        while not self._stop.wait(self.poll):
            try:
                size = os.path.getsize(self.path)
            except OSError:
                continue
            if size == self._alert_size:
                continue
            self._alert_size = size
            try:
                st = S.build(T.parse(self.path))
            except Exception:
                continue
            msg = _fmt_alert(S.diff(self._alert_state, st))
            self._alert_state = st
            if msg:
                with self._lock:
                    sys.stdout.write("\n🔔 " + msg + "\nyou> ")
                    sys.stdout.flush()

    # ---- REPL ------------------------------------------------------------
    def loop(self):
        try:
            import readline  # noqa: F401  (enables history/arrow keys if present)
        except Exception:
            pass
        self.refresh()
        print(f"🛰  cc-copilot chat — attached to {os.path.basename(self.path)}")
        print(self.banner())
        have_llm = N.available(self.backend)
        print(f"backend: {N.backend_name(self.backend)}")
        if not have_llm:
            sys.stderr.write("# backend unavailable; questions need one "
                             "(`cc-copilot backends`). /brief /check /diff still work.\n")
        print("ask a question, or /help.  Ctrl-D to exit.\n")
        self._start_alerts()
        try:
            while True:
                try:
                    line = input("you> ").strip()
                except EOFError:
                    print()
                    break
                if not line:
                    continue
                if line.startswith("/"):
                    out = self.meta(line)
                    if out is False:
                        break
                    with self._lock:
                        print(out + "\n")
                    continue
                if not have_llm:
                    with self._lock:
                        print("# no LLM backend — set CC_COPILOT_LLM_CMD or "
                              "install the claude CLI.\n")
                    continue
                with self._lock:
                    print("…", flush=True)
                try:
                    ans = self.answer(line)
                except Exception as e:
                    with self._lock:
                        print(f"# error: {e}\n")
                    continue
                with self._lock:
                    print(self.banner())
                    print(ans + "\n")
        finally:
            self._stop.set()
