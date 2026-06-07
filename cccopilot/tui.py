"""The full-screen "cockpit" TUI (optional `cc-copilot[tui]` extra).

A Textual app that ports OpenCode-style polish onto cc-copilot's read-only
observer: a branded theme, a split **agent-timeline / chat** layout, per-role
left-gutter message blocks, a status bar with a colored **verdict pill** + a
`Footer`, a multiline composer, `/`-commands in the command palette, watcher
**toasts**, collapsible long output, modal session/model pickers, and Markdown
answers — with `[L<n>]` citation fidelity preserved throughout.

It reuses the deterministic core + the `ChatSession` controller verbatim; only
the I/O surface changes. Textual is imported lazily so the core stays zero-dep.
"""

from __future__ import annotations

import os
import threading

try:
    from textual import events, on, work
    from textual.app import App, ComposeResult, SystemCommand
    from textual.binding import Binding
    from textual.containers import Vertical, VerticalScroll
    from textual.message import Message
    from textual.screen import ModalScreen
    from textual.theme import Theme
    from textual.widgets import (Collapsible, Footer, Input, Markdown, OptionList,
                                 Static, TextArea)
    from textual.widgets.option_list import Option
    from rich.text import Text
except ImportError:
    raise SystemExit(
        "the cockpit TUI needs Textual. Run:  cc-copilot setup\n"
        "(or: pip install 'cc-copilot[tui]')")

from . import (transcript as T, state as S, assess as A, narrate as N,
               backends as BK, store as ST)
from .chat import _fmt_alert, _fmt_diff, _GLYPH, _dur


# ── theme (single branded palette; everything references semantic tokens) ──
COCKPIT_THEME = Theme(
    name="cockpit",
    primary="#fab283", secondary="#5c9cf5", accent="#9d7cd8",
    foreground="#c0caf5", background="#1a1b26", surface="#1f2335", panel="#24283b",
    success="#9ece6a", warning="#e0af68", error="#f7768e", dark=True,
    variables={
        "verdict-intervene": "#f7768e", "verdict-review": "#e0af68",
        "verdict-clear": "#9ece6a", "verdict-idle": "#565f89",
        "verdict-awaiting": "#7aa2f7", "verdict-empty": "#565f89",
    },
)
_STATUS_GLYPH = {"running": "●", "stalled": "■", "awaiting-agent": "◆",
                 "idle": "○", "empty": "·"}
# concrete hex (Rich Text styles can't resolve Textual $variables) — mirrors the theme
_PAL = {"primary": "#fab283", "secondary": "#5c9cf5", "accent": "#9d7cd8",
        "muted": "#565f89", "error": "#f7768e", "warning": "#e0af68",
        "success": "#9ece6a", "text": "#c0caf5", "bg": "#1a1b26"}
_VERDICT_HEX = {"intervene": "#f7768e", "review": "#e0af68", "clear": "#9ece6a",
                "idle": "#565f89", "awaiting": "#7aa2f7", "empty": "#565f89"}

_HELP_TEXT = (
    "ask a question (multiline: Shift+Enter / Ctrl+J · send: Enter)\n"
    "commands (also in the command palette, Ctrl+P):\n"
    "  /brief /check /diff     recap · safety · changes (LLM-free)\n"
    "  /sessions               switch which session you observe   (Ctrl+S)\n"
    "  /history                browse & restore past conversations (Ctrl+H)\n"
    "  /model [name]           switch backend                     (Ctrl+T)\n"
    "  /use <n|id>  /refresh   /quit\n"
    "keys: Ctrl+R refresh · Ctrl+L clear · Ctrl+C quit")


# ── multiline composer ─────────────────────────────────────────────────────
class Composer(TextArea):
    class Submitted(Message):
        def __init__(self, text: str):
            self.text = text
            super().__init__()

    def __init__(self, **kw):
        super().__init__(soft_wrap=True, show_line_numbers=False,
                         tab_behavior="focus", **kw)

    async def _on_key(self, event: events.Key) -> None:
        # Enter submits. Shift+Enter / Ctrl+J insert a newline (TextArea's own
        # newline is bound to plain Enter, which we've taken for submit, so we
        # have to insert it ourselves). Everything else — including all CJK /
        # multilingual typing — falls through to TextArea's handler.
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.strip()
            self.text = ""
            if text:
                self.post_message(self.Submitted(text))
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)


# ── reusable fuzzy-filter picker modal ─────────────────────────────────────
class Picker(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, title: str, options: list):
        super().__init__()
        self._title = title
        self._options = options            # [(label, value), …]
        self._by_id = {str(i): v for i, (l, v) in enumerate(options)}

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static(self._title, id="picker-title")
            yield Input(placeholder="filter…", id="picker-filter")
            yield OptionList(
                *[Option(l, id=str(i)) for i, (l, v) in enumerate(self._options)],
                id="picker-list")

    def on_mount(self):
        self.query_one("#picker-filter", Input).focus()

    @on(Input.Changed, "#picker-filter")
    def _filter(self, event: Input.Changed):
        q = event.value.lower()
        ol = self.query_one("#picker-list", OptionList)
        ol.clear_options()
        for i, (label, _) in enumerate(self._options):
            if q in label.lower():
                ol.add_option(Option(label, id=str(i)))

    @on(OptionList.OptionSelected)
    def _choose(self, event: OptionList.OptionSelected):
        self.dismiss(self._by_id.get(event.option.id))

    def action_cancel(self):
        self.dismiss(None)


# ── the cockpit ────────────────────────────────────────────────────────────
class Cockpit(App):
    CSS = """
    Screen { layout: vertical; }

    #timeline {
        dock: top; height: 8;
        border-bottom: solid $accent;
        background: $panel; padding: 0 1;
    }
    #timeline-title { color: $accent; text-style: bold; }
    #chat { height: 1fr; background: $surface; padding: 0 1; }

    /* status + composer flow at the bottom (above the docked Footer); no
       competing dock:bottom so the composer box is always visible. */
    #status { height: 1; background: $boost; color: $text; padding: 0 1; }
    #composer {
        height: auto; min-height: 3; max-height: 8;
        border: round $accent; padding: 0 1; margin: 0 1;
        background: $surface;
    }
    #composer:focus-within { border: round $primary; }

    .role-user      { border-left: thick $secondary; padding-left: 1; }
    .role-assistant { border-left: thick $primary;   padding-left: 1; }
    .role-event     { border-left: tall  $accent;    padding-left: 1; }
    .role-alert     { border-left: thick $warning;   padding-left: 1; }
    Collapsible { border-left: thick $accent; }

    Picker { align: center middle; }
    #picker { width: 80; max-width: 90%; height: auto; max-height: 80%;
              background: $surface; border: round $accent; padding: 1; }
    #picker-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #picker-list { height: auto; max-height: 20; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "quit"),
        Binding("ctrl+r", "refresh_now", "refresh"),
        Binding("ctrl+l", "clear_chat", "clear"),
        Binding("ctrl+s", "sessions", "sessions"),
        Binding("ctrl+t", "model", "model"),
        Binding("ctrl+h", "history", "history"),
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

    # ---- layout ----
    def compose(self) -> ComposeResult:
        timeline = VerticalScroll(
            Static("agent timeline — window-1", id="timeline-title"), id="timeline")
        chat = VerticalScroll(id="chat")
        # The timeline and chat are display-only. Keep them out of the focus
        # chain so a click (or Tab) can never strand focus on a scroll pane —
        # that used to leave typed / IME (e.g. Chinese) input with no target.
        # Mouse-wheel scrolling still works without focus.
        timeline.can_focus = chat.can_focus = False
        yield timeline
        yield chat
        yield Static("", id="status")
        yield Composer(id="composer")
        yield Footer()

    def on_mount(self):
        self.register_theme(COCKPIT_THEME)
        self.theme = "cockpit"
        self.session.refresh()
        self.title = "cc-copilot cockpit"
        self.sub_title = os.path.basename(self.session.path)[:8]
        self._chat(self._role(Text(f"attached to {os.path.basename(self.session.path)} · "
                                   f"backend {N.backend_name(self.backend).split(' (')[0]}",
                                   "dim"), "role-event"))
        self._rebuild_chat(clear=False)        # repaint any restored prior dialogue
        if not N.available(self.backend):
            self.notify("backend unavailable — /model to switch", severity="warning")
        self._update_status()
        composer = self.query_one("#composer", Composer)
        composer.border_title = "› ask the copilot"
        composer.border_subtitle = "Enter send · ⇧+Enter newline · Ctrl+P palette"
        composer.focus()
        if self.alerts:
            self.watch_agent()

    def on_unmount(self):
        self._watch_stop.set()

    # ---- focus: a click anywhere (or re-entering the app) lands on the
    #      composer, so the user never has to aim at the box, and IME /
    #      multilingual typing always has somewhere to go. ----
    def _focus_composer(self) -> None:
        if len(self.screen_stack) > 1:
            return  # a modal picker / palette is up — don't steal its focus
        try:
            composer = self.query_one("#composer", Composer)
        except Exception:
            return
        if not composer.has_focus:
            composer.focus()

    def on_click(self, event: events.Click) -> None:
        self._focus_composer()

    def on_app_focus(self, event: events.AppFocus) -> None:
        self._focus_composer()

    # ---- command palette ----
    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        yield SystemCommand("Brief", "Evidence-cited recap", self.action_brief)
        yield SystemCommand("Check", "Safety / off-track assessment", self.action_check)
        yield SystemCommand("Diff", "What changed since last turn", self.action_diff)
        yield SystemCommand("Sessions", "Pick a session to observe", self.action_sessions)
        yield SystemCommand("History", "Browse past copilot conversations", self.action_history)
        yield SystemCommand("Model", "Switch the LLM backend", self.action_model)
        yield SystemCommand("Refresh", "Re-read the session now", self.action_refresh_now)

    # ---- render helpers ----
    def _role(self, renderable, cls):
        w = Static(renderable, classes=cls)
        return w

    def _chat(self, widget):
        chat = self.query_one("#chat", VerticalScroll)
        chat.mount(widget)
        chat.scroll_end(animate=False)

    def _timeline(self, renderable, cls="role-event"):
        tl = self.query_one("#timeline", VerticalScroll)
        tl.mount(Static(renderable, classes=cls))
        tl.scroll_end(animate=False)

    def _rebuild_chat(self, clear=True):
        """Repaint the chat pane from the session's (possibly restored) history,
        the SAME way live turns render — so switching/relaunching shows prior
        dialogue instead of losing it. Markdown re-render keeps [L…] citations."""
        chat = self.query_one("#chat", VerticalScroll)
        if clear:
            chat.remove_children()
        hist = self.session.history
        if hist:
            n = len(hist) // 2
            chat.mount(self._role(
                Text(f"── restored {n} prior turn{'s' if n != 1 else ''} ──", "dim"),
                "role-event"))
            for role, txt in hist:
                if role == "user":
                    chat.mount(self._role(Text("› " + txt, style="bold"), "role-user"))
                else:
                    chat.mount(Markdown(txt, classes="role-assistant"))
        chat.scroll_end(animate=False)

    def _update_status(self):
        st = self.session.st
        t = Text()
        if st is None:                         # history-only (transcript gone)
            t.append(" ⌁ history-only ", style="bold")
            t.append(" transcript gone ", style=f"bold {_PAL['bg']} on {_PAL['warning']}")
            t.append("  ")
            be = N.backend_name(self.backend).split(" (")[0]
            t.append(be + (":" + self.model if self.model else ""), style=_PAL["secondary"])
            self.query_one("#status", Static).update(t)
            return
        a = A.assess(st)
        t.append(f" {_STATUS_GLYPH.get(st.status, '·')} {st.status} ", style="bold")
        t.append("  ")
        t.append(f" {a.verdict.upper()} ",
                 style=f"bold {_PAL['bg']} on {_VERDICT_HEX.get(a.verdict, _PAL['muted'])}")
        t.append("  ")
        be = N.backend_name(self.backend).split(" (")[0]
        t.append(be + (":" + self.model if self.model else ""), style=_PAL["secondary"])
        t.append(f"   idle {_dur(st.idle_seconds)} · {st.tr.raw_lines} ev",
                 style=_PAL["muted"])
        if self._busy:
            t.append("   ⠿ working", style=_PAL["accent"])
        self.query_one("#status", Static).update(t)

    # ---- input ----
    @on(Composer.Submitted)
    def _on_submit(self, event: Composer.Submitted):
        text = event.text
        if text.startswith("/"):
            self._meta(text)
            return
        self._chat(self._role(Text("› " + text, style="bold"), "role-user"))
        if self._busy:
            self.notify("still answering the previous question", severity="warning")
            return
        if self.session.st is None:
            self.notify("history-only view (transcript gone) — /sessions to attach a "
                        "live session", severity="warning")
            return
        if not N.available(self.backend):
            self.notify("no backend — /model to switch", severity="error")
            return
        self.session.refresh()
        self._busy = True
        self._update_status()
        self._answer(text, self.session.st, list(self.session.history))

    @work(thread=True)
    def _answer(self, text, st, history):
        try:
            ans, ok = N.chat(st, history, text, model=self.model, backend=self.backend), True
        except Exception as e:
            ans, ok = f"# error: {e}", False
        self.call_from_thread(self._answer_done, text, ans, ok)

    def _answer_done(self, text, ans, ok):
        self._busy = False
        if ok:
            self.session.history.append(("user", text))
            self.session.history.append(("assistant", ans))
            self._chat(Markdown(ans, classes="role-assistant"))
            # the cockpit's single durable write-site (the REPL has its own in
            # ChatSession.answer); _answer runs on a worker thread, hence here.
            self.session.store.record_turn(text, ans, st=self.session.st,
                                            backend=self.backend, model=self.model)
        else:
            self._chat(self._role(Text(ans, style=_PAL["error"]), "role-alert"))
        self._update_status()

    # ---- background watcher ----
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
        for fc in d.new_changed[:4]:
            self._timeline(Text(f"✎ {os.path.basename(fc.path)}  ({fc.total} edit/write)  [L{fc.last_line}]"))
        for f in d.new_failures[:4]:
            self._timeline(Text(f"✗ {f.tool} failed  [L{f.line}]", style=_PAL["error"]), "role-alert")
        msg = _fmt_alert(d)
        if msg:
            sev = "error" if "INTERVENE" in msg or "STALLED" in msg else "warning"
            self.notify(msg, severity=sev, title="window-1")

    # ---- meta commands (typed `/…` still works) ----
    def _meta(self, cmd):
        low = cmd.strip().lower()
        if low in ("/quit", "/exit", "/q"):
            self.exit(); return
        if low in ("/help", "/?"):
            self._collapsible("/help", _HELP_TEXT); return
        if low == "/brief":
            self.action_brief(); return
        if low == "/check":
            self.action_check(); return
        if low == "/diff":
            self.action_diff(); return
        if low in ("/sessions", "/session"):
            self.action_sessions(); return
        if low == "/history" or low.startswith("/history"):
            self.action_history(); return
        if low == "/model" or low.startswith("/model "):
            arg = cmd.strip()[6:].strip()
            if arg:
                self._set_backend(arg)
            else:
                self.action_model()
            return
        if low == "/refresh":
            self.action_refresh_now(); return
        if low.startswith("/use"):
            out = self.session.meta(cmd)
            self.notify(str(out).splitlines()[0])
            self._rebuild_chat()       # restore the switched-to session's dialogue
            self._update_status(); return
        self.notify(f"unknown command {cmd!r}", severity="warning")

    def _collapsible(self, title, body):
        renderable = body if isinstance(body, Text) else Text(str(body))
        self._chat(Collapsible(Static(renderable), title=title, collapsed=False))

    def _diff_renderable(self, d):
        if (d.new_events == 0 and not d.new_changed and not d.new_failures
                and d.status_from == d.status_to):
            return Text("(no change since last turn)", style=_PAL["muted"])
        t = Text()
        t.append(f"+{d.new_events} events", style=_PAL["muted"])
        if d.status_from != d.status_to:
            t.append(f"\nstatus  {d.status_from or '∅'} → {d.status_to}", style=_PAL["secondary"])
        if d.verdict_from != d.verdict_to:
            t.append(f"\nsafety  {d.verdict_from or '∅'} → {d.verdict_to}",
                     style=_VERDICT_HEX.get(d.verdict_to, _PAL["muted"]))
        for fc in d.new_changed[:8]:
            t.append(f"\n  ~ {fc.path} ({fc.total} edit/write) [L{fc.last_line}]",
                     style=_PAL["success"])
        for f in d.new_failures[:6]:
            t.append(f"\n  ✗ {f.tool} [L{f.line}]: {f.summary[:70]}", style=_PAL["error"])
        return t

    def _no_live(self) -> bool:
        if self.session.st is None:        # history-only (transcript gone)
            self.notify("history-only view — /sessions to attach a live session",
                        severity="warning")
            return True
        return False

    def action_brief(self):
        self.session.refresh()
        if self._no_live():
            return
        self._collapsible("/brief — recap", B_render(self.session.st))
        self._update_status()

    def action_check(self):
        self.session.refresh()
        if self._no_live():
            return
        self._collapsible("/check — safety", _check_text(self.session.st))
        self._update_status()

    def action_diff(self):
        self.session.refresh()
        if self._no_live():
            return
        self._collapsible("/diff — changes since last turn",
                          self._diff_renderable(S.diff(self.session.prev, self.session.st)))

    @work
    async def action_sessions(self):
        opts = []
        for p in self.session.siblings():
            try:
                kb = os.path.getsize(p) // 1024
            except OSError:
                kb = 0
            cur = " (current)" if os.path.abspath(p) == os.path.abspath(self.session.path) else ""
            opts.append((f"{os.path.basename(p)[:-6][:8]}  {kb} KB{cur}", p))
        chosen = await self.push_screen_wait(Picker("switch session", opts))
        if chosen:
            self.session.switch_path(chosen)   # restores chosen session's history
            self._rebuild_chat()               # repaint it (the old data-loss site)
            self.sub_title = os.path.basename(chosen)[:-6][:8]
            self._update_status()
            self.notify(f"→ {os.path.basename(chosen)[:8]}")

    @work
    async def action_history(self):
        headers = ST.list_conversations(getattr(self.session, "cwd", None) or None)
        if not headers:
            self.notify("no saved copilot conversations yet"); return
        opts = []
        for h in headers:
            gone = "  (gone)" if not h.transcript_present else ""
            proj = os.path.basename(h.cwd) or "?"
            opts.append((f"{(h.title or '(untitled)')[:32]:<32} · {h.turns:>2}t · "
                         f"{h.ago()} · {proj}{gone}", h))
        chosen = await self.push_screen_wait(Picker("history — past conversations", opts))
        if chosen:
            live = self.session.attach_conv(chosen)
            self._rebuild_chat()
            self.sub_title = chosen.conv_id[:8]
            self._update_status()
            self.notify(("→ " if live else "history-only → ") + chosen.conv_id[:8],
                        severity="information" if live else "warning")

    @work
    async def action_model(self):
        opts = [(f"{name}{'  ✓' if be.available() else '  · ' + be.reason()}", name)
                for name, be in sorted(BK.registry().items())]
        chosen = await self.push_screen_wait(Picker("switch backend", opts))
        if chosen:
            self._set_backend(chosen)

    def _set_backend(self, name):
        try:
            be = BK.resolve(name)
        except BK.BackendError as e:
            self.notify(str(e), severity="error"); return
        self.backend = self.session.backend = name
        self.notify(f"backend → {name}", severity="information")
        self._update_status()

    def action_refresh_now(self):
        self.session.refresh()
        self._update_status()
        self.notify("history-only — no live session" if self.session.st is None
                    else "refreshed")

    def action_clear_chat(self):
        # Visual only — clears the pane, never the on-disk history. (Don't wire a
        # store delete here; Ctrl+L is muscle-memory for "tidy the screen".)
        self.query_one("#chat", VerticalScroll).remove_children()


# small adapters so the cockpit doesn't reach into private chat internals
def B_render(st):
    from .brief import render
    return render(st)


def _check_text(st):
    from .brief import render_check
    return render_check(st)


def run(session, poll=5, alerts=True):
    Cockpit(session, poll=poll, alerts=alerts).run()
