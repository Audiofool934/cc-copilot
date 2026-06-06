"""The full-screen "cockpit" TUI (optional `cc-copilot[tui]` extra).

Mirrors Codex's single async event-loop and Claude Code's pinned-input / pinned-
status model, in Python via Textual. It reuses the deterministic core and the
``ChatSession`` controller verbatim — only the I/O surface changes:

- a **reactive header** (status glyph · safety verdict · backend:model · idle),
- a **scrolling log** that interleaves the live agent timeline and your chat,
- a **background watcher worker** that re-parses window-1's growing JSONL and
  pushes ``state.diff`` alerts (the old ``chat._alert_loop``, now off-UI-thread),
- a **backend worker** that runs the (default: codex) turn without freezing the UI.

Textual is imported lazily so the core/CLI stay zero-dependency.
"""

from __future__ import annotations

import os
import threading

try:
    from textual.app import App, ComposeResult
    from textual.widgets import RichLog, Input, Static
    from textual.reactive import reactive
    from textual import work, on
    from rich.text import Text
except ImportError:
    raise SystemExit(
        "the cockpit TUI needs Textual — install the extra:\n"
        "  pip install 'cc-copilot[tui]'\n"
        "or in this repo:  python3 -m venv .venv && .venv/bin/pip install textual")

from . import transcript as T, state as S, assess as A, narrate as N, backends as BK
from .chat import _fmt_alert, _GLYPH, _dur, _HELP as _REPL_HELP

_VERDICT_STYLE = {
    "intervene": "bold white on red", "review": "black on yellow",
    "clear": "black on green", "idle": "dim", "awaiting": "black on yellow",
    "empty": "dim",
}

_HELP = (
    "commands (all but questions are LLM-free):\n"
    "  /brief /check /diff        recap · safety · changes\n"
    "  /sessions   /use <n|id>    list · switch session\n"
    "  /model <name>              switch backend (codex/claude/deepseek/ollama/…)\n"
    "  /refresh   /quit\n"
    "keys: Enter send · Ctrl+R refresh · Ctrl+L clear · Ctrl+C quit"
)


class Cockpit(App):
    CSS = """
    Screen { layout: vertical; }
    #status { dock: top; height: 1; background: $panel; color: $text; padding: 0 1; }
    #log { height: 1fr; padding: 0 1; background: $surface; }
    #composer { dock: bottom; border: round $accent; }
    """
    BINDINGS = [
        ("ctrl+c", "quit", "quit"),
        ("ctrl+l", "clear_log", "clear"),
        ("ctrl+r", "refresh_now", "refresh"),
    ]

    def __init__(self, session, poll=5, alerts=True):
        super().__init__()
        self.session = session
        self.backend = session.backend
        self.model = session.model
        self.poll = max(2, poll)
        self.alerts = alerts
        self._busy = False
        self._watch_stop = threading.Event()

    # ---- layout ----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        yield RichLog(id="log", markup=False, wrap=True, auto_scroll=True, highlight=False)
        yield Input(placeholder="ask the copilot…   (/help · Ctrl+C quit)", id="composer")

    def on_mount(self):
        self.session.refresh()
        self.title = "cc-copilot cockpit"
        self._post(Text(f"🛰  cc-copilot cockpit — attached to "
                        f"{os.path.basename(self.session.path)}", style="bold"))
        self._post(Text(f"backend: {N.backend_name(self.backend)}", style="dim"))
        if not N.available(self.backend):
            self._post(Text("backend unavailable — /model <name> to switch; "
                            "/brief /check /diff still work.", style="yellow"))
        self._post(Text("type a question, or /help.", style="dim"))
        self._update_status()
        self.query_one("#composer", Input).focus()
        if self.alerts:
            self.watch_agent()

    def on_unmount(self):
        self._watch_stop.set()

    # ---- render helpers --------------------------------------------------
    def _post(self, renderable):
        self.query_one("#log", RichLog).write(renderable)

    def _line(self, glyph, gstyle, body, bstyle=""):
        t = Text()
        t.append(glyph + " ", style=gstyle)
        t.append(body, style=bstyle)
        return t

    def _update_status(self):
        st = self.session.st
        a = A.assess(st)
        t = Text()
        t.append(_GLYPH.get(st.status, "?") + " ", style="bold")
        t.append(st.status, style="bold")
        t.append("  ")
        t.append(f" {a.verdict.upper()} ", style=_VERDICT_STYLE.get(a.verdict, "dim"))
        t.append("  ")
        t.append(N.backend_name(self.backend).split(" (")[0], style="cyan")
        if self.model:
            t.append(":" + self.model, style="cyan")
        t.append(f"   idle {_dur(st.idle_seconds)} · {st.tr.raw_lines} ev", style="dim")
        if self._busy:
            t.append("   ⠹ working", style="yellow")
        self.query_one("#status", Static).update(t)

    # ---- input -----------------------------------------------------------
    @on(Input.Submitted, "#composer")
    def _on_submit(self, event: Input.Submitted):
        text = event.value.strip()
        self.query_one("#composer", Input).value = ""
        if not text:
            return
        if text.startswith("/"):
            self._meta(text)
            return
        self._post(self._line("›", "bold", text))
        if self._busy:
            self._post(Text("…still answering the previous question", style="yellow"))
            return
        if not N.available(self.backend):
            self._post(Text("# no backend available — /model <name>", style="red"))
            return
        self.session.refresh()
        self._busy = True
        self._update_status()
        self._post(Text("⠹ …", style="dim"))
        self._answer(text, self.session.st, list(self.session.history))

    @work(thread=True)
    def _answer(self, text, st, history):
        try:
            ans = N.chat(st, history, text, model=self.model, backend=self.backend)
            style = "white"
        except Exception as e:
            ans, style = f"# error: {e}", "red"
        self.call_from_thread(self._answer_done, text, ans, style)

    def _answer_done(self, text, ans, style):
        self._busy = False
        if style != "red":
            self.session.history.append(("user", text))
            self.session.history.append(("assistant", ans))
        self._post(self._line("▌", "bold " + style, ans, style if style == "red" else ""))
        self._update_status()

    # ---- background watcher (window-1's live timeline) -------------------
    @work(thread=True, exclusive=True, group="watch")
    def watch_agent(self):
        last_size = self.session.last_size
        last_state = self.session.st
        while not self._watch_stop.wait(self.poll):
            try:
                size = os.path.getsize(self.session.path)
            except OSError:
                continue
            if size == last_size:
                continue
            last_size = size
            try:
                st = S.build(T.parse(self.session.path))
            except Exception:
                continue
            d = S.diff(last_state, st)
            last_state = st
            self.call_from_thread(self._on_watch, st, d)

    def _on_watch(self, st, d):
        self.session.st = st
        self._update_status()
        msg = _fmt_alert(d)
        if msg:
            self._post(self._line("ⓘ", "yellow", msg, "yellow"))

    # ---- meta commands ---------------------------------------------------
    def _meta(self, cmd):
        low = cmd.strip().lower()
        if low in ("/quit", "/exit", "/q"):
            self.exit()
            return
        if low == "/help":
            self._post(Text(_HELP, style="dim"))
            return
        if low == "/model" or low.startswith("/model "):
            self._set_model(cmd.strip()[6:].strip())
            return
        out = self.session.meta(cmd)        # /brief /check /diff /sessions /use /refresh …
        if out is False:
            self.exit()
            return
        self._post(Text(str(out), style="dim"))
        self._update_status()

    def _set_model(self, arg):
        if not arg:
            self._post(Text(f"backend: {N.backend_name(self.backend)}", style="dim"))
            return
        try:
            be = BK.resolve(arg)
        except BK.BackendError as e:
            self._post(Text(str(e), style="red"))
            return
        self.backend = self.session.backend = arg
        self._post(Text(f"backend → {be.describe()}"
                        + ("" if be.available() else "  (unavailable: " + be.reason() + ")"),
                        style="cyan"))
        self._update_status()

    # ---- key bindings ----------------------------------------------------
    def action_clear_log(self):
        self.query_one("#log", RichLog).clear()

    def action_refresh_now(self):
        self.session.refresh()
        self._update_status()
        self._post(Text("(refreshed) " + self.session.banner(), style="dim"))


def run(session, poll=5, alerts=True):
    Cockpit(session, poll=poll, alerts=alerts).run()
