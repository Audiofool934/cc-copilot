"""The persistent, parallel, read-only chat sidecar (`cc-copilot chat`).

Pin to one *other* session and hold an ongoing QA conversation while the copilot
shares the observed session's LIVE timeline: every turn re-parses the (growing)
JSONL, so an answer can never lag the agent. A background thread watches the file
and pushes inline "it just stalled / went off-track" alerts so it feels parallel,
not pull-only.

Read-only with respect to the observed agent: the transcript is opened for
reading only, and there is no handle to the observed agent's process. Separate
copilot Q&A history may be persisted under cc-copilot's own state directory.
"""

from __future__ import annotations

import os
import sys
import threading

from . import (transcript as T, state as S, brief as B, assess as A,
               narrate as N, locate as LOC, store as ST, scope as SC)

_GLYPH = {"running": "🟢", "stalled": "🔴", "awaiting-agent": "🟡",
          "idle": "⚪", "empty": "∅"}

_HELP = """commands (all but questions are LLM-free):
  /brief            full evidence-cited recap
  /check            safety verdict + friction signals
  /diff             what changed since your last turn
  /refresh          re-read the session now
  /scope [name]     show or set grounding scope: session, multi-session, project
  /session          which session is attached
  /sessions         list other sessions in this project
  /use <n|id>       switch to another session (restores its prior chat)
  /history          this chat's turns
  /history this     past copilot conversations in this project
  /history all      past copilot conversations across every project
  /forget           delete THIS conversation's saved history
  /rewind [n]       fork from an earlier message (list, or re-ask #n)
  /help             this
  /exit  /quit      leave  (Ctrl-D also works)
anything else → a question answered grounded in the selected read-only scope."""


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
        bits.append(f"observed session → {d.status_to.upper()}")
    if d.verdict_to == "intervene" and d.verdict_from != "intervene":
        bits.append("safety → INTERVENE")
    if d.new_failures:
        f = d.new_failures[-1]
        bits.append(f"{len(d.new_failures)} new error(s), e.g. {f.tool} [L{f.line}]")
    if not bits:
        return ""
    tail = f"  (+{d.new_events} events)" if d.new_events else ""
    return " · ".join(bits) + tail


def _fmt_conv_list(headers, scope="") -> str:
    """One row per saved copilot conversation (newest first)."""
    if not headers:
        where = f" for {scope}" if scope else ""
        return (f"(no saved copilot conversations{where})\n"
                f"  history dir: {ST.state_home()}")
    out = ["saved copilot conversations" + (f" — {scope}" if scope else "")
           + f"  ({len(headers)}):"]
    for h in headers:
        gone = "  (transcript gone)" if not h.transcript_present else ""
        proj = os.path.basename(h.cwd) or "?"
        out.append(f"  {h.conv_id[:8]}  {LOC.ago(h.updated):>5} ago  {h.turns:>3}t  "
                   f"{(h.title or '(untitled)')[:40]:<40}  {proj}{gone}")
    return "\n".join(out)


class ChatSession:
    def __init__(self, path, model=None, backend=None, alerts=True, poll=5,
                 persist=True, scope=SC.SESSION, scope_sessions=None):
        self.path = path
        self.model = model
        self.backend = backend
        self.scope = SC.normalize(scope)
        self.scope_sessions = []
        self.poll = max(2, poll)
        self.history = []          # [(role, text)] — restored from the store in _attach
        self.cwd = ""
        self.st = None
        self.prev = None
        self.last_size = -1
        self._alerts = alerts
        self._persist = persist and ST.enabled()
        self.store = ST.Store.open_for(path, enabled=self._persist)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._alert_size = -1
        self._alert_state = None
        self._thread = None
        self._attach(path)         # load state + restore prior copilot dialogue
        if scope_sessions:
            self.set_scope_sessions(scope_sessions)

    # ---- live state ------------------------------------------------------
    def refresh(self) -> bool:
        """Re-read the session if it grew. Returns True if it changed. Tolerates a
        gone/unreadable transcript (history-only mode) by leaving ``st`` None."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = -1
        if size == self.last_size and self.st is not None:
            return False
        self.last_size = size
        self.prev = self.st
        try:
            self.st = S.build(T.parse(self.path))
        except OSError:                    # transcript gone — stay in history-only mode
            self.st = None
            return False
        return True

    def banner(self) -> str:
        st = self.st
        if st is None:
            return f"[scope: {self.scope_label()} · ∅ no live session — transcript gone (history-only)]"
        return (f"[scope: {self.scope_label()} · {_GLYPH.get(st.status, '?')} {st.status} · idle "
                f"{_dur(st.idle_seconds)} · {st.tr.raw_lines} ev · "
                f"safety: {A.assess(st).verdict}]")

    def scope_label(self) -> str:
        if self.scope in (SC.MULTI, SC.PROJECT) and self.scope_sessions:
            return f"{self.scope}:{len(self.scope_sessions)}"
        return self.scope

    def evidence(self, st=None):
        return SC.render_evidence(self.path, self.st if st is None else st,
                                  self.scope, sessions=self.scope_sessions)

    def answer(self, q: str) -> str:
        self.refresh()
        ev = self.evidence()
        txt = N.chat_brief(ev.text, self.history, q, model=self.model, backend=self.backend)
        self.history.append(("user", q))
        self.history.append(("assistant", txt))
        # durable copilot history (best-effort; never breaks the answer)
        self.store.record_turn(q, txt, st=self.st, backend=self.backend, model=self.model)
        return txt

    def meta(self, cmd: str):
        """Handle a /command. Returns text to print, or False to exit."""
        c = cmd.strip().lower()
        if c in ("/exit", "/quit"):
            return False
        self.refresh()
        if self.st is None and c in ("/check", "/diff"):
            return "(no live session — transcript gone; history-only view)"
        if c == "/help":
            return _HELP
        if c == "/brief":
            return self.evidence().text
        if c == "/check":
            return B.render_check(self.st) if self.scope == SC.SESSION else self.evidence().text
        if c == "/refresh":
            return self.banner() + "  (refreshed)"
        if c == "/scope" or c.startswith("/scope "):
            return self._scope(cmd.strip()[6:].strip())
        if c == "/session":
            return f"attached: {self.path}\nscope: {self.scope_label()}\n{self.banner()}"
        if c == "/sessions":
            return self._list_sessions()
        if c.startswith("/use"):
            return self._switch(cmd.strip()[4:].strip())
        if c == "/history" or c.startswith("/history "):
            arg = c[8:].strip()
            if arg in ("all", "*", "this", "project"):
                if not self.store.enabled:
                    return "(history is off — --no-persist or [history] enabled=false)"
                if arg in ("all", "*"):
                    return _fmt_conv_list(ST.list_conversations(None), "all projects")
                cwd = self.cwd or os.getcwd()
                return _fmt_conv_list(ST.list_conversations(cwd), cwd)
            if not self.history:
                return "(no turns yet — try `/history this` for past conversations)"
            return "\n".join(("you> " if r == "user" else "cc > ") + t[:200]
                             for r, t in self.history)
        if c == "/diff":
            return _fmt_diff(S.diff(self.prev, self.st))
        if c == "/forget":
            if not self.store.enabled:
                return "(history is off — nothing saved to forget)"
            self.store.delete()
            self.history = []
            return "forgot this conversation's saved history"
        if c == "/rewind" or c.startswith("/rewind "):
            qs = [t for r, t in self.history if r == "user"]
            if not qs:
                return "(nothing to rewind — no questions yet)"
            arg = c[7:].strip()
            if not arg.isdigit():
                lines = ["rewind to which message?  `/rewind <n>` re-asks it:"]
                lines += [f"  {i}. {q[:60]}" for i, q in enumerate(qs, 1)]
                return "\n".join(lines)
            k = int(arg) - 1
            if not (0 <= k < len(qs)):
                return f"no message #{arg} (have 1–{len(qs)})"
            question = qs[k]
            self.history = self.history[:2 * k]
            self.store.truncate(k)
            return (f"rewound to before message #{k + 1}. Ask it again, edited as you like:\n"
                    f"  {question}")
        return f"unknown command {cmd!r} — try /help"

    def _scope(self, arg):
        if not arg:
            selected = ("selected sessions: " + ", ".join(s[:8] for s in self.scope_sessions)
                        if self.scope_sessions else "selected sessions: all")
            return ("scope: " + self.scope_label() + "\n"
                    + selected + "\n"
                    "available: session, multi-session, project\n"
                    "usage: /scope <session|multi-session|project> [session-id|prefix ...]\n"
                    "       /scope all   # clear the selected subset")
        toks = arg.split()
        if len(toks) == 1 and toks[0].lower() in ("all", "*", "clear", "reset"):
            self.scope_sessions = []
            return f"scope → {self.scope_label()}\n{self.banner()}"
        try:
            new_scope = SC.normalize(toks[0])
            selectors = toks[1:]
        except ValueError as e:
            if self.scope in (SC.MULTI, SC.PROJECT):
                new_scope = self.scope
                selectors = toks
            else:
                return str(e)
        self.scope = new_scope
        if self.scope == SC.SESSION:
            self.scope_sessions = []
        elif not selectors or selectors in (["all"], ["*"]):
            self.scope_sessions = []
        else:
            try:
                self.set_scope_sessions(selectors)
            except ValueError as e:
                return str(e)
        return f"scope → {self.scope_label()}\n{self.banner()}"

    def set_scope_sessions(self, selectors) -> None:
        refs = SC.resolve_session_refs(self.path, SC.parse_selectors(selectors))
        self.scope_sessions = [r.session_id for r in refs]

    # ---- session switching (select among multiple sessions) --------------
    def _siblings(self):
        d = os.path.dirname(self.path)
        refs = LOC.refs_in_dir(d, include_own=True)
        # hide cc-copilot's own narration sessions, but never hide the one we're
        # currently attached to.
        refs = [r for r in refs if not r.own or r.path == self.path]
        self._session_refs = refs
        self._listing = [r.path for r in refs]
        return self._listing

    def sibling_refs(self):
        """Public list of sibling sessions with title metadata."""
        self._siblings()
        return getattr(self, "_session_refs", [])

    def _list_sessions(self):
        refs = self.sibling_refs()
        out = ["sessions in this project (newest first — `/use <n|id>`):"]
        for i, r in enumerate(refs, 1):
            p = r.path
            cur = "*" if os.path.samefile(p, self.path) else " "
            title = (r.title or "(untitled)")[:50]
            out.append(f" {cur}{i:>2}. {title:<50}  {r.session_id[:8]}  {r.size // 1024:>6} KB")
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
        n = len(self.history) // 2
        restored = f"restored {n} prior turn{'s' if n != 1 else ''}" if n else "no prior chat"
        return (f"switched → {os.path.basename(target)[:-6][:8]} "
                f"({restored})\n{self.banner()}")

    def switch_path(self, path):
        """Re-pin to another session: fresh state, and RESTORE that session's
        prior copilot dialogue from disk (was previously wiped — the data loss
        the user hit when switching)."""
        self._attach(path)

    def _attach(self, path):
        """Point at ``path``: reset live state, re-open the store for it, and
        load any persisted copilot history. Survives a missing transcript."""
        self.path = path
        self.st = self.prev = None
        self.last_size = -1
        self._alert_state = None
        self._alert_size = -1
        self.refresh()                       # st may stay None if the file is gone
        tr = getattr(self.st, "tr", None)
        self.store = ST.Store.open_for(path, enabled=self._persist, tr=tr)
        self.history = self.store.load_history()
        self.cwd = (getattr(tr, "cwd", "") or LOC.read_cwd(path) or "")

    def attach_conv(self, header) -> bool:
        """Attach by a stored conversation header (from /history). Returns True
        for a live re-attach, False when the transcript is gone (history-only)."""
        if header.transcript and os.path.isfile(header.transcript):
            self._attach(header.transcript)
            return True
        self.path = header.transcript
        self.st = self.prev = None
        self.last_size = -1
        self.store = ST.Store(header.conv_id, enabled=self._persist)
        self.store.transcript = header.transcript
        self.history = self.store.load_history()
        self.cwd = header.cwd
        return False

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
