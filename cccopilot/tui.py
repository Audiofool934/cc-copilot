"""The full-screen "cockpit" TUI (optional `cc-copilot[tui]` extra).

A Textual app that ports OpenCode-style polish onto cc-copilot's read-only
observer: a branded theme, a split **observed-activity / chat** layout, per-role
left-gutter message blocks, a status bar with a colored **verdict pill** + a
`Footer`, a multiline composer, `/`-commands in the command palette, watcher
**toasts**, collapsible long output, modal session/model pickers, and Markdown
answers — with `[L<n>]` citation fidelity preserved throughout.

It reuses the deterministic core + the `ChatSession` controller verbatim; only
the I/O surface changes. Textual is imported lazily so the core stays zero-dep.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading

# Disable the Kitty keyboard protocol by default. Its "associated text" feature
# encodes IME-committed input (e.g. multi-character Chinese from pinyin) as
# colon-separated codepoints that Textual's parser mishandles, leaking raw
# escapes like `[49;;29616:22312u` into the box. Plain UTF-8 (the fallback)
# handles multilingual input correctly. Cost: we lose Shift+Enter newline
# disambiguation, so Ctrl+J is the newline key. Set TEXTUAL_DISABLE_KITTY_KEY=0
# to re-enable Kitty (and Shift+Enter) if you never type via an IME.
os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")

try:
    from textual import events, on, work
    from textual.app import App, ComposeResult, SystemCommand
    from textual.binding import Binding
    from textual.containers import Vertical, VerticalScroll
    from textual.css.query import NoMatches
    from textual.message import Message
    from textual.screen import ModalScreen
    from textual.theme import Theme
    from textual.widgets import (Collapsible, Footer, Input, Markdown, OptionList,
                                 RichLog, Static, TextArea)
    from textual.widgets.option_list import Option
    from rich.text import Text
except ImportError:
    raise SystemExit(
        "the cockpit TUI needs Textual. Run:  cc-copilot setup\n"
        "(or: pip install 'cc-copilot[tui]')")

from . import (sources as SRC, state as S, assess as A, narrate as N,
               backends as BK, store as ST, scope as SC, locate as LOC,
               observe as O, context as EC, prefs as PREFS)
from .chat import _fmt_alert, _fmt_diff, _GLYPH, _dur


# ── themes (small curated set; everything references semantic tokens) ──
COCKPIT_THEME_SPECS = {
    "cockpit": {
        "label": "Cockpit",
        # neutral graphite ground; the accent is the Claude×Codex midpoint
        # (#cb7d5b + #347ff2 averaged → a muted lavender) — the copilot's own
        # color is literally the blend of the two agents it watches.
        "description": "graphite, apricot, blue, and the Claude×Codex blend",
        "primary": "#fab283", "secondary": "#5c9cf5", "accent": "#807ea6",
        # panel == background: the main panes (header/timeline/chat) sit FLUSH on
        # the #1e1e1e (30,30,30) ground instead of floating a lighter #2d2d2d
        # layer over it — so what fills the screen is the asked-for color, and
        # separation comes from the borders, not a low-contrast filled rectangle.
        # surface gives the composer a subtle lift; $boost is translucent in
        # Textual so the status strip just blends (which suits the flat ground).
        "foreground": "#c0caf5", "background": "#1e1e1e",
        "surface": "#262626", "panel": "#1e1e1e", "boost": "#353535",
        "success": "#9ece6a", "warning": "#e0af68", "error": "#f7768e",
        "muted": "#6c7086", "dark": True,
    },
    "graphite": {
        "label": "Graphite",
        "description": "charcoal, steel, cyan, and amber",
        "primary": "#8bd5ca", "secondary": "#8aadf4", "accent": "#f5a97f",
        "foreground": "#d7dee8", "background": "#111318",
        "surface": "#191d24", "panel": "#20252d", "boost": "#272d36",
        "success": "#a6da95", "warning": "#eed49f", "error": "#ed8796",
        "muted": "#7a8494", "dark": True,
    },
    "signal": {
        "label": "Signal",
        "description": "near-black, green, blue, and coral",
        "primary": "#7dd3a8", "secondary": "#7aa2f7", "accent": "#ffb86c",
        "foreground": "#d8e2dc", "background": "#0f1412",
        "surface": "#151c19", "panel": "#1d2521", "boost": "#24302a",
        "success": "#9ece6a", "warning": "#e0af68", "error": "#ff7b72",
        "muted": "#738078", "dark": True,
    },
    "daybreak": {
        "label": "Daybreak",
        "description": "light, quiet, blue, and persimmon",
        "primary": "#2f6f9f", "secondary": "#4f7f52", "accent": "#b85c38",
        "foreground": "#1f2933", "background": "#f5f7fa",
        "surface": "#eef2f6", "panel": "#e4eaf0", "boost": "#dae3ec",
        "success": "#3f7d4f", "warning": "#9a6b16", "error": "#b23a48",
        "muted": "#697586", "dark": False,
    },
}


def _theme_from_spec(name: str, spec: dict) -> Theme:
    return Theme(
        name=name,
        primary=spec["primary"], secondary=spec["secondary"], accent=spec["accent"],
        foreground=spec["foreground"], background=spec["background"],
        surface=spec["surface"], panel=spec["panel"], boost=spec["boost"],
        success=spec["success"], warning=spec["warning"], error=spec["error"],
        dark=spec.get("dark", True),
        variables={
            "verdict-intervene": spec["error"], "verdict-review": spec["warning"],
            "verdict-clear": spec["success"], "verdict-idle": spec["muted"],
            "verdict-awaiting": spec["secondary"], "verdict-empty": spec["muted"],
        },
    )


COCKPIT_THEMES = tuple(
    _theme_from_spec(name, spec) for name, spec in COCKPIT_THEME_SPECS.items()
)
COCKPIT_THEME_NAMES = tuple(COCKPIT_THEME_SPECS)
_STATUS_GLYPH = {"running": "●", "stalled": "■", "awaiting-agent": "◆",
                 "idle": "○", "empty": "·"}
# concrete hex (Rich Text styles can't resolve Textual $variables) — mirrors the theme
def _rich_palette(name: str) -> dict:
    spec = COCKPIT_THEME_SPECS.get(name, COCKPIT_THEME_SPECS["cockpit"])
    return {
        "primary": spec["primary"], "secondary": spec["secondary"],
        "accent": spec["accent"], "muted": spec["muted"],
        "error": spec["error"], "warning": spec["warning"],
        "success": spec["success"], "text": spec["foreground"],
        "bg": spec["background"],
    }


def _verdict_palette(name: str) -> dict:
    spec = COCKPIT_THEME_SPECS.get(name, COCKPIT_THEME_SPECS["cockpit"])
    return {"intervene": spec["error"], "review": spec["warning"],
            "clear": spec["success"], "idle": spec["muted"],
            "awaiting": spec["secondary"], "empty": spec["muted"]}


def _theme_name(name: str = "") -> str:
    key = (name or "").strip().lower()
    return key if key in COCKPIT_THEME_SPECS else "cockpit"


_PAL = _rich_palette("cockpit")
_VERDICT_HEX = _verdict_palette("cockpit")
_BUSY_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_TIMELINE_TITLE = "session activity"

# Per-agent identity hues — the *watched* agent's brand color (Claude's
# apricot-rust, Codex's blue), applied to agent-identity spans: the timeline
# `agent` label and the header's "<agent> session". These are theme-independent
# (an agent's brand is the agent's brand). Unknown agents fall back to the
# copilot's own accent so its chrome color shows through instead of a stray hue.
_AGENT_HEX = {"claude": "#cb7d5b", "codex": "#347ff2"}


def _agent_hex(agent: str) -> str:
    return _AGENT_HEX.get((agent or "").strip().lower(), _PAL["accent"])

_HELP_TEXT = (
    "ask a question (newline: Ctrl+J · send: Enter)\n"
    "type `/` for command suggestions (Enter accepts, Tab completes; palette: Ctrl+P):\n"
    "  /observe /brief /check  attention · recap · safety (LLM-free)\n"
    "  /since [30m] [--raw]    recap since you last looked (--raw = cited delta)\n"
    "  /handoff [file]         shareable Markdown handoff\n"
    "  /diff                   changes since last turn\n"
    "  /sessions               choose evidence sessions\n"
    "  /here                   observe your own current (live) session\n"
    "  /resume                 resume a cockpit session\n"
    "  /new                    start a new cockpit session\n"
    "  /theme                  switch cockpit palette\n"
    "  /rewind                 fork the chat from an earlier message (Esc on empty)\n"
    "  /model [name]           switch backend                     (Ctrl+T)\n"
    "  /use <n|id>  /refresh   /forget   /quit\n"
    "keys: Ctrl+R refresh · Ctrl+L clear view · Shift+↑/↓ resize timeline · Ctrl+C quit")

# Slash commands, shown in the `/` autocomplete (name, one-line help, takes-arg).
_SLASH_CMDS = [
    ("/observe", "attention queue + next human decision", False),
    ("/since", "recap since you last looked (or 30m / 2h; --raw = cited delta)", True),
    ("/handoff", "shareable Markdown handoff (brief + what changed)", True),
    ("/brief", "evidence-cited recap (LLM-free)", False),
    ("/check", "safety / off-track verdict (LLM-free)", False),
    ("/diff", "what changed since your last turn", False),
    ("/sessions", "choose one or more evidence sessions", False),
    ("/here", "observe your own current (live) session", False),
    ("/resume", "browse & resume cockpit sessions", False),
    ("/new", "start a new independent cockpit session", False),
    ("/theme", "switch cockpit palette", False),
    ("/model", "switch the LLM backend", True),
    ("/use", "change evidence session by number / id", True),
    ("/rewind", "fork from an earlier message (or Esc on empty input)", False),
    ("/refresh", "re-read the observed session now", False),
    ("/forget", "delete THIS cockpit session's saved state", False),
    ("/clear", "clear the chat view (keeps saved history)", False),
    ("/help", "show help", False),
    ("/quit", "exit the cockpit", False),
]
_ARG_CMDS = {c for c, _, takes in _SLASH_CMDS if takes}


def _session_picker_label(ref, current_path: str = "") -> str:
    title = ref.title or "(untitled)"
    cur = " (current)" if current_path and os.path.abspath(ref.path) == os.path.abspath(current_path) else ""
    live = " ⟵ your live session" if getattr(ref, "live", False) else ""
    agent = f"{getattr(ref, 'agent', 'claude'):<6}"
    return f"{agent} · {title[:40]:<40} · {ref.session_id[:8]} · {ref.size // 1024} KB{cur}{live}"


def _session_selection_ids(session, refs: list) -> list:
    ids = {r.session_id for r in refs}
    if getattr(session, "scope", SC.SESSION) in (SC.MULTI, SC.PROJECT):
        selected = [sid for sid in getattr(session, "scope_sessions", []) if sid in ids]
        return selected or [r.session_id for r in refs]
    here = os.path.abspath(getattr(session, "path", "") or "")
    current = next((r.session_id for r in refs if os.path.abspath(r.path) == here), "")
    return [current] if current else []


def _apply_session_selection(session, refs: list, selected_ids: list) -> str:
    refs_by_id = {r.session_id: r for r in refs}
    chosen = [sid for sid in selected_ids if sid in refs_by_id]
    if not chosen:
        raise ValueError("select at least one evidence session")

    anchor_id = os.path.basename(getattr(session, "path", "") or "")[:-6]
    if anchor_id not in chosen:
        session.switch_path(refs_by_id[chosen[0]].path)
        anchor_id = chosen[0]

    if len(chosen) == 1:
        sid = chosen[0]
        if sid != anchor_id:
            session.switch_path(refs_by_id[sid].path)
        session.scope = SC.SESSION
        session.scope_sessions = []
        msg = f"evidence → {sid[:8]}"
    else:
        session.scope = SC.MULTI
        session.scope_sessions = [] if len(chosen) == len(refs) else chosen
        msg = (f"evidence → all {len(refs)} sessions"
               if not session.scope_sessions
               else f"evidence → {len(chosen)} selected sessions")
    session._persist_state()
    return msg


def _busy_indicator(frame: int) -> str:
    return f"{_BUSY_FRAMES[frame % len(_BUSY_FRAMES)]} answering"


def _assemble(segs) -> Text:
    """Join ``(text, style|None)`` spans into one Text — style None = a raw
    separator (no color). Used to build the status bar from atomic field spans so
    nothing is ever split mid-field, and so the result can be measured (.cell_len)
    to decide whether it fits the current width."""
    t = Text()
    for txt, sty in segs:
        t.append(txt) if sty is None else t.append(txt, style=sty)
    return t


def _short_activity(text: str, limit: int = 70) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Timeline rows are unwrapped and pan horizontally, so they keep far more than
# the compact status-line summaries — a long path / command / error stays
# readable by scrolling sideways. Still capped so one pathological multi-KB blob
# can't make the virtual width (and the pan distance) absurd.
_TIMELINE_LINE_MAX = 200


def _tool_activity_target(record) -> str:
    inp = record.tool_input if isinstance(record.tool_input, dict) else {}
    if record.tool_name == "Bash":
        return inp.get("description") or inp.get("command") or ""
    if record.tool_name == "TodoWrite":
        return "todos"
    return inp.get("file_path") or inp.get("notebook_path") or ""


def _activity_line(record, agent_hex=None):
    # agent_hex: the source session's brand color for its `agent` label (Claude
    # rust / Codex blue). Read _PAL at call time (it's a mutable global swapped on
    # theme switch), so it can't be a default-arg value.
    agent_hex = agent_hex or _PAL["primary"]
    if record.kind == "human" and not record.housekeeping:
        t = Text(f"{record.hhmm} ", style=_PAL["muted"])
        t.append("user", style=_PAL["secondary"])
        t.append(" · " + _short_activity(record.text, _TIMELINE_LINE_MAX), style=_PAL["text"])
        return t
    if record.kind == "agent_text":
        t = Text(f"{record.hhmm} ", style=_PAL["muted"])
        t.append("agent", style=agent_hex)
        t.append(" · " + _short_activity(record.text, _TIMELINE_LINE_MAX), style=_PAL["text"])
        return t
    if record.kind == "agent_thinking":
        return Text(f"{record.hhmm} agent thinking", style=_PAL["muted"])
    if record.kind == "tool_call":
        target = _short_activity(_tool_activity_target(record), _TIMELINE_LINE_MAX)
        t = Text(f"{record.hhmm} ", style=_PAL["muted"])
        t.append(record.tool_name or "tool", style=_PAL["accent"])
        if target:
            t.append(" · " + target, style=_PAL["text"])
        return t
    if record.kind == "tool_result" and record.is_error:
        t = Text(f"{record.hhmm} ", style=_PAL["muted"])
        t.append("tool error", style=_PAL["error"])
        if record.text:
            t.append(" · " + _short_activity(record.text, _TIMELINE_LINE_MAX), style=_PAL["text"])
        return t
    return None


def _recent_activity_lines(st, limit=None, agent_hex=None) -> list:
    """Activity lines for the timeline, oldest→newest. ``limit`` None = the whole
    session (the RichLog timeline holds it all and scrolls). ``agent_hex`` colors
    the `agent` label with the watched session's brand hue."""
    if st is None:
        return []
    rows = []
    for record in reversed(getattr(st.tr, "records", [])):
        line = _activity_line(record, agent_hex)
        if line is None:
            continue
        rows.append(line)
        if limit is not None and len(rows) >= limit:
            break
    rows.reverse()
    return rows


# gutter-bar color per line class — mirrors the line's own semantics (error/red,
# warn/amber, else accent), matching the per-level text color instead of painting
# every alert red the way the first RichLog cut did.
_GUTTER_BAR = {"role-alert": "error", "role-warn": "warning"}


def _timeline_gutter(renderable, cls: str = "role-event") -> Text:
    """Prefix a timeline line with a colored gutter bar, the RichLog-line
    equivalent of the old per-row left border. The bar color is scoped to just
    the ``▌`` prefix span: a base ``style=`` on the whole Text would underlie —
    and tint — every colorless span of the line (status, file-change), so it is
    applied only to the glyph."""
    bar = _PAL[_GUTTER_BAR.get(cls, "accent")]
    t = Text.assemble(("▌ ", bar))
    t.append_text(renderable if isinstance(renderable, Text) else Text(str(renderable)))
    return t


def _sid(ref=None, st=None, path: str = "") -> str:
    sid = ""
    tr = getattr(st, "tr", None)
    if tr is not None:
        sid = getattr(tr, "session_id", "") or sid
    sid = sid or getattr(ref, "session_id", "")
    if not sid and path:
        sid = os.path.basename(path)[:-6]
    return (sid[:8] or "session")


def _sub_title(session) -> str:
    """Title-bar subtitle: which agent + the session id prefix."""
    return f"{_agent_of(session)} {_sid(st=getattr(session, 'st', None), path=getattr(session, 'path', '') or '')}"


def _session_title(st=None, ref=None) -> str:
    tr = getattr(st, "tr", None)
    title = getattr(tr, "title", "") if tr is not None else ""
    title = title or getattr(ref, "title", "")
    intents = getattr(st, "intents", None)
    if not title and intents:
        title = getattr(intents[-1], "text", "")
    return title or "(untitled)"


def _project_cwd(session) -> str:
    st = getattr(session, "st", None)
    tr = getattr(st, "tr", None)
    cwd = (getattr(tr, "cwd", "") if tr is not None else "")
    cwd = cwd or getattr(session, "cwd", "") or SRC.read_cwd(getattr(session, "path", "") or "")
    return os.path.abspath(cwd or os.getcwd())


def _git(root: str, *args: str) -> str:
    if not root or not os.path.isdir(root):
        return ""
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _git_summary(root: str) -> tuple:
    branch = _git(root, "branch", "--show-current") or _git(root, "rev-parse", "--short", "HEAD")
    status = _git(root, "status", "--short")
    changed = len(status.splitlines()) if status else 0
    return branch or "?", changed


def _scope_rank(status: str, verdict: str) -> int:
    if status == "stalled" or verdict == "intervene":
        return 0
    if status == "awaiting-agent":
        return 1
    if status == "running":
        return 2 if verdict == "review" else 3
    if verdict == "review":
        return 4
    if status == "idle":
        return 5
    return 6


def _scope_snapshot(session) -> dict:
    scope = getattr(session, "scope", SC.SESSION)
    if scope == SC.SESSION:
        st = getattr(session, "st", None)
        a = A.assess(st) if st is not None else None
        return {"scope": scope, "total": 1 if st is not None else 0,
                "selected": 1 if st is not None else 0, "items": [],
                "anchor": (None, st, a), "error": ""}

    try:
        all_refs = SC.resolve_session_refs(session.path, [])
        refs = SC.resolve_session_refs(session.path, session.scope_sessions)
    except ValueError as e:
        return {"scope": scope, "total": 0, "selected": 0, "items": [],
                "anchor": (None, getattr(session, "st", None), None), "error": str(e)}

    here = os.path.abspath(session.path) if getattr(session, "path", "") else ""
    items = []
    for ref in refs:
        try:
            st = (session.st if os.path.abspath(ref.path) == here and session.st is not None
                  else S.build(SRC.parse(ref.path)))
            a = A.assess(st)
        except Exception:
            continue
        items.append((ref, st, a))
    items.sort(key=lambda x: (_scope_rank(x[1].status, x[2].verdict),
                              x[1].idle_seconds if x[1].idle_seconds is not None else 9e9,
                              -x[0].mtime))
    anchor_a = A.assess(session.st) if getattr(session, "st", None) is not None else None
    return {"scope": scope, "total": len(all_refs), "selected": len(items),
            "items": items, "anchor": (None, getattr(session, "st", None), anchor_a),
            "error": ""}


def _health_bits(items: list) -> list:
    counts = {}
    verdicts = {}
    for _ref, st, a in items:
        counts[st.status] = counts.get(st.status, 0) + 1
        verdicts[a.verdict] = verdicts.get(a.verdict, 0) + 1
    bits = []
    for status in ("stalled", "running", "awaiting-agent", "idle"):
        n = counts.get(status, 0)
        if n:
            bits.append(f"{n} {status}")
    for verdict in ("intervene", "review"):
        n = verdicts.get(verdict, 0)
        if n:
            bits.append(f"{n} {verdict}")
    return bits or ["no activity"]


def _agent_of(session) -> str:
    """Which agent wrote the session the cockpit is currently watching."""
    path = getattr(session, "path", "") or ""
    if not path:
        return "claude"
    try:
        return SRC.source_for_path(path).name
    except Exception:
        return "claude"


def _agent_mix(items: list) -> str:
    """Compact agent breakdown across a multi-session selection, or '' if one."""
    counts = {}
    for ref, _st, _a in items:
        ag = getattr(ref, "agent", "claude")
        counts[ag] = counts.get(ag, 0) + 1
    if len(counts) <= 1:
        return ""
    return " · ".join(f"{n} {ag}" for ag, n in sorted(counts.items(), key=lambda x: -x[1]))


def _selection_label(session, snap: dict) -> str:
    selected, total = snap.get("selected", 0), snap.get("total", 0)
    if getattr(session, "scope", SC.SESSION) == SC.SESSION:
        return "current session"
    if getattr(session, "scope_sessions", None):
        return f"{selected} selected of {total}"
    return f"all {total}"


def _scope_activity_title(session, snap=None) -> str:
    snap = snap or _scope_snapshot(session)
    if getattr(session, "scope", SC.SESSION) == SC.SESSION:
        return "session activity"
    if session.scope == SC.MULTI:
        return f"multi-session activity · {_selection_label(session, snap)}"
    return f"project activity · {_selection_label(session, snap)}"


def _prefixed_activity_line(sid: str, line) -> Text:
    # append_text (not append(str(line), …)) — preserve the line's own spans,
    # incl. the per-agent `agent` hue, instead of flattening the whole row to one
    # muted color. Without this a mixed-agent scoped timeline loses its brand tints.
    t = Text(f"{sid} · ", style=_PAL["muted"])
    t.append_text(line if isinstance(line, Text) else Text(str(line)))
    return t


def _scoped_recent_activity_lines(items: list, limit: int = 5) -> list:
    entries = []
    for ref, st, _a in items:
        hexcol = _agent_hex(getattr(ref, "agent", "claude"))
        for record in reversed(getattr(st.tr, "records", [])):
            line = _activity_line(record, hexcol)
            if line is None:
                continue
            ts = record.ts.timestamp() if record.ts is not None else 0
            entries.append((ts, _sid(ref, st), line))
            break
    entries.sort(key=lambda x: x[0])
    return [_prefixed_activity_line(sid, line) for _ts, sid, line in entries[-limit:]]


def _scope_timeline_summary(session, snap: dict) -> Text:
    if snap.get("error"):
        return Text("scope error · " + snap["error"], style=_PAL["warning"])
    items = snap.get("items", [])
    t = Text()
    t.append(f"{_selection_label(session, snap)} sessions", style=_PAL["muted"])
    t.append(" · " + " · ".join(_health_bits(items)), style=_PAL["text"])
    return t


def _timeline_status_line(st):
    if st is None:
        return Text("⌁ history-only · transcript unavailable", style=_PAL["warning"])
    t = Text()
    t.append(f"{_STATUS_GLYPH.get(st.status, '·')} {st.status}", style="bold")
    t.append(f" · {st.tr.raw_lines} events · idle {_dur(st.idle_seconds)}",
             style=_PAL["muted"])
    return t


def _timeline_delta_line(st, d):
    t = Text(f"+{d.new_events} events", style=_PAL["muted"])
    if d.status_from != d.status_to:
        t.append(f" · {d.status_from or 'empty'} → {d.status_to}",
                 style=_PAL["secondary"])
    else:
        t.append(f" · {st.status}", style=_PAL["muted"])
    if d.verdict_from != d.verdict_to:
        t.append(f" · safety {d.verdict_from or 'empty'} → {d.verdict_to}",
                 style=_VERDICT_HEX.get(d.verdict_to, _PAL["muted"]))
    return t


def _observer_timeline_line(level: str, text: str) -> Text:
    style = {"alarm": _PAL["error"], "warn": _PAL["warning"],
             "info": _PAL["secondary"], "clear": _PAL["success"]}.get(level, _PAL["text"])
    t = Text("attention · ", style=_PAL["muted"])
    t.append(text, style=style)
    return t


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
        # When the `/` suggestion popup is open, the arrow/Tab/Esc keys drive it
        # instead of the text cursor.
        app = self.app
        if getattr(app, "_slash_open", False):
            if event.key == "down":
                event.prevent_default(); event.stop(); app._slash_move(1); return
            if event.key == "up":
                event.prevent_default(); event.stop(); app._slash_move(-1); return
            if event.key == "tab":
                event.prevent_default(); event.stop(); app._slash_complete(); return
            if event.key == "enter":
                event.prevent_default(); event.stop(); app._slash_accept(); return
            if event.key == "escape":
                event.prevent_default(); event.stop(); app._slash_hide(); return
        # Esc on an empty composer rewinds the conversation (Codex-style): fork
        # from an earlier message. Only when there's something to rewind to.
        if (event.key == "escape" and not self.text.strip()
                and any(r == "user" for r, _ in app.session.history)):
            event.prevent_default(); event.stop()
            app.action_rewind()
            return
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
class PickerFilter(Input):
    async def _on_key(self, event: events.Key) -> None:
        toggle = getattr(self.screen, "_toggle_from_filter", None)
        if event.key == "space" and not self.value and toggle is not None:
            event.prevent_default()
            event.stop()
            toggle()
            return
        await super()._on_key(event)


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
            yield PickerFilter(placeholder="filter…", id="picker-filter")
            yield OptionList(
                *[Option(l, id=str(i)) for i, (l, v) in enumerate(self._options)],
                id="picker-list")

    def on_mount(self):
        self.query_one("#picker-filter", Input).focus()
        self._highlight(0)

    def _list(self) -> OptionList:
        return self.query_one("#picker-list", OptionList)

    def _highlight(self, index: int) -> None:
        ol = self._list()
        if not ol.option_count:
            return
        ol.highlighted = max(0, min(ol.option_count - 1, index))

    def _move(self, delta: int) -> None:
        ol = self._list()
        if not ol.option_count:
            return
        cur = ol.highlighted if ol.highlighted is not None else 0
        self._highlight(cur + delta)

    def _choose_highlighted(self) -> None:
        ol = self._list()
        if not ol.option_count:
            return
        idx = ol.highlighted if ol.highlighted is not None else 0
        self.dismiss(self._by_id.get(ol.get_option_at_index(idx).id))

    async def _on_key(self, event: events.Key) -> None:
        """Keep typing in the filter, but make row selection keyboard-first."""
        if event.key in ("down", "ctrl+n"):
            event.prevent_default(); event.stop()
            self._move(1)
            return
        if event.key in ("up", "ctrl+p"):
            event.prevent_default(); event.stop()
            self._move(-1)
            return
        if event.key == "pagedown":
            event.prevent_default(); event.stop()
            self._move(8)
            return
        if event.key == "pageup":
            event.prevent_default(); event.stop()
            self._move(-8)
            return
        if event.key == "home":
            event.prevent_default(); event.stop()
            self._highlight(0)
            return
        if event.key == "end":
            event.prevent_default(); event.stop()
            self._highlight(10**9)
            return
        if event.key == "enter":
            event.prevent_default(); event.stop()
            self._choose_highlighted()
            return

    @on(Input.Changed, "#picker-filter")
    def _filter(self, event: Input.Changed):
        q = event.value.lower()
        ol = self._list()
        ol.clear_options()
        for i, (label, _) in enumerate(self._options):
            if q in label.lower():
                ol.add_option(Option(label, id=str(i)))
        if ol.option_count:
            ol.highlighted = 0

    @on(OptionList.OptionSelected)
    def _choose(self, event: OptionList.OptionSelected):
        self.dismiss(self._by_id.get(event.option.id))

    def action_cancel(self):
        self.dismiss(None)


class MultiPicker(ModalScreen):
    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("space", "toggle", "toggle", show=False),
    ]

    def __init__(self, title: str, options: list, selected=None):
        super().__init__()
        self._title = title
        self._options = options            # [(label, value), …]
        self._selected = set(selected or [])

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static(self._title, id="picker-title")
            yield PickerFilter(placeholder="filter…", id="picker-filter")
            yield OptionList(id="picker-list")
            yield Static("", id="picker-hint")

    def on_mount(self):
        self.query_one("#picker-filter", Input).focus()
        self._render_options()
        self._highlight(0)
        self._refresh_chrome()

    def _list(self) -> OptionList:
        return self.query_one("#picker-list", OptionList)

    def _option_text(self, label: str, selected: bool) -> Text:
        # style the checkbox + label explicitly so the mark stays legible under
        # the highlight bar and selected rows are obvious at a glance
        t = Text()
        t.append("[x] " if selected else "[ ] ",
                 style=f"bold {_PAL['success']}" if selected else _PAL["muted"])
        t.append(label, style=f"bold {_PAL['text']}" if selected else _PAL["text"])
        return t

    def _render_options(self) -> None:
        q = self.query_one("#picker-filter", Input).value.lower()
        ol = self._list()
        ol.clear_options()
        for i, (label, value) in enumerate(self._options):
            if q and q not in label.lower():
                continue
            ol.add_option(Option(self._option_text(label, value in self._selected), id=str(i)))
        if ol.option_count:
            ol.highlighted = 0

    def _refresh_chrome(self) -> None:
        """Keep the title count + the key hint current so the picker explains
        itself — the #1 confusion was not knowing Space toggles (Enter confirms)."""
        n = len(self._selected)
        try:
            self.query_one("#picker-title", Static).update(
                f"{self._title}   ({n} selected)")
            hint = Text()
            hint.append("Space", style=f"bold {_PAL['accent']}")
            hint.append(" / click toggle · ", style=_PAL["muted"])
            hint.append("Enter", style=f"bold {_PAL['accent']}")
            hint.append(" confirm · ", style=_PAL["muted"])
            hint.append("Esc", style=f"bold {_PAL['accent']}")
            hint.append(" cancel · type to filter", style=_PAL["muted"])
            self.query_one("#picker-hint", Static).update(hint)
        except Exception:
            pass

    def _highlight(self, index: int) -> None:
        ol = self._list()
        if ol.option_count:
            ol.highlighted = max(0, min(ol.option_count - 1, index))

    def _move(self, delta: int) -> None:
        ol = self._list()
        if ol.option_count:
            cur = ol.highlighted if ol.highlighted is not None else 0
            self._highlight(cur + delta)

    def _value_at_highlight(self):
        ol = self._list()
        if not ol.option_count:
            return None
        idx = ol.highlighted if ol.highlighted is not None else 0
        opt_id = ol.get_option_at_index(idx).id
        return self._options[int(opt_id)][1] if opt_id is not None else None

    def _toggle(self) -> None:
        value = self._value_at_highlight()
        if value is None:
            return
        if value in self._selected:
            self._selected.remove(value)
        else:
            self._selected.add(value)
        keep = self._list().highlighted or 0
        self._render_options()
        self._highlight(keep)
        self._refresh_chrome()

    def _selected_values(self) -> list:
        return [value for _label, value in self._options if value in self._selected]

    async def _on_key(self, event: events.Key) -> None:
        if event.key in ("down", "ctrl+n"):
            event.prevent_default(); event.stop(); self._move(1); return
        if event.key in ("up", "ctrl+p"):
            event.prevent_default(); event.stop(); self._move(-1); return
        if event.key == "space":
            event.prevent_default(); event.stop(); self._toggle(); return
        if event.key == "enter":
            event.prevent_default(); event.stop()
            self.dismiss(self._selected_values())
            return

    @on(Input.Changed, "#picker-filter")
    def _filter(self, event: Input.Changed):
        self._render_options()

    @on(OptionList.OptionSelected)
    def _choose(self, event: OptionList.OptionSelected):
        self._toggle()

    def action_cancel(self):
        self.dismiss(None)

    def action_toggle(self):
        self._toggle()

    def _toggle_from_filter(self):
        self._toggle()


# ── the cockpit ────────────────────────────────────────────────────────────
class Cockpit(App):
    CSS = """
    Screen { layout: vertical; }

    #status-header {
        height: 3;
        border-bottom: solid $secondary;
        background: $panel; padding: 0 1;
    }
    #timeline {
        height: 6;
        border-bottom: solid $accent;
        /* left pad only — no RIGHT pad, so the log's scrollbar reaches the screen
           edge and lines up with #chat's (which sits at the edge too). */
        background: $panel; padding: 0 0 0 1;
    }
    #timeline-title { color: $accent; text-style: bold; height: 1; }
    #timeline-log {
        height: 1fr; background: $panel;
        overflow-x: auto;              /* long lines (wrap=False) stay scrollable… */
        scrollbar-size-horizontal: 0;  /* …but draw NO horizontal bar (pan by wheel/trackpad) */
        scrollbar-size-vertical: 1;    /* thin vertical bar */
    }
    /* same $panel as the timeline so the two panes read as one surface (not a
       separate block), and a 1-cell scrollbar that aligns with the timeline's. */
    #chat {
        /* width:100% — VerticalScroll defaults to width:1fr, which reserves the
           scrollbar gutter and leaves the pane 2 cols short of its siblings, so
           its scrollbar floated off the edge; 100% fills the width and lands the
           scrollbar at the screen edge, aligned with the timeline's. Left pad
           only (no right pad) so nothing sits between content and that edge. */
        width: 100%; height: 1fr; background: $panel; padding: 0 0 0 1;
        scrollbar-size-vertical: 1;
    }

    /* status + composer flow at the bottom (above the docked Footer); no
       competing dock:bottom so the composer box is always visible. */
    /* height:auto so a narrow sidebar can reflow the status into stacked rows
       (no field gets cropped); bounded so the long HUD can't starve #chat.
       text-wrap:wrap is the safety net for an over-long single field. */
    #status {
        height: auto; min-height: 1; max-height: 8;
        background: $boost; color: $text; padding: 0 1; text-wrap: wrap;
    }
    #composer {
        height: auto; min-height: 3; max-height: 8;
        border: round $accent; padding: 0 1; margin: 0 1;
        background: $surface;
    }
    #composer:focus-within { border: round $primary; }
    #slash { height: auto; max-height: 7; margin: 0 1; padding: 0;
             border: round $secondary; background: $panel; }

    /* `outer` renders a left half-block ▌ — same glyph as the timeline gutter,
       so the chat bars match its width (thick/█ and tall/▊ read heavier). */
    .role-user      { border-left: outer $secondary; padding-left: 1; }
    .role-assistant { border-left: outer $primary;   padding-left: 1; }
    .role-event     { border-left: outer $accent;    padding-left: 1; }
    .role-alert     { border-left: outer $warning;   padding-left: 1; }
    Collapsible { border-left: outer $accent; }

    Picker { align: center middle; }
    MultiPicker { align: center middle; }
    #picker { width: 80; max-width: 90%; height: auto; max-height: 80%;
              background: $surface; border: round $accent; padding: 1; }
    #picker-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #picker-list { height: auto; max-height: 20; }
    #picker-hint { margin-top: 1; color: $text-muted; }

    /* The default highlight bar is near-invisible against the dark surface, and
       dimmer still when the list isn't focused (the filter has focus). Force a
       clearly-distinct primary-tinted band + bold, regardless of focus, so the
       cursor row is obvious in every theme. */
    OptionList > .option-list--option-highlighted,
    OptionList:focus > .option-list--option-highlighted {
        background: $secondary 45%;
        text-style: bold;
    }
    OptionList > .option-list--option-hover {
        background: $primary 20%;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "quit"),
        Binding("ctrl+r", "refresh_now", "refresh"),
        Binding("ctrl+l", "clear_chat", "clear view"),
        Binding("ctrl+t", "model", "model"),
        # resize the activity timeline (the chat fills the rest); persisted.
        # priority so it works while the composer is focused. Shift+arrows are
        # the primary keys — macOS grabs Ctrl+Up/Down for Mission Control, so
        # those stay as a hidden alias for platforms where they get through.
        Binding("shift+up", "grow_timeline", "taller", priority=True),
        Binding("shift+down", "shrink_timeline", "shorter", priority=True),
        Binding("ctrl+up", "grow_timeline", "taller", priority=True, show=False),
        Binding("ctrl+down", "shrink_timeline", "shorter", priority=True, show=False),
    ]

    TIMELINE_MIN = 3
    TIMELINE_MAX = 24
    TIMELINE_DEFAULT = 6

    def __init__(self, session, poll=2, alerts=True):
        super().__init__()
        self.session = session
        self.backend = session.backend
        self.model = session.model
        self.poll = max(1, poll)
        self.alerts = alerts
        self._busy = False
        self._busy_frame = 0
        self._ctx_stats = None
        self._out_tokens = 0
        self._slash_open = False
        self._watch_stop = threading.Event()
        self._watch_path = None
        self._watch_size = -1
        self._watch_state = None
        self._timeline_sig = None       # evidence identity of the last rebuild

    # ---- layout ----
    def compose(self) -> ComposeResult:
        header = Static("", id="status-header")
        # The activity log is a RichLog (not one widget per line) so it can hold
        # the *entire* session history efficiently and scroll through all of it;
        # the title stays pinned above it.
        # wrap=False: each event stays on one row and long lines pan with a (thin)
        # horizontal scrollbar instead of folding. min_width=1 so that bar appears
        # ONLY when a line truly exceeds the panel width — the default 78 would
        # force a spurious 1-column scroll even for short lines on an 80-col term.
        timeline_log = RichLog(id="timeline-log", markup=False, highlight=False,
                               wrap=False, auto_scroll=False, min_width=1)
        timeline_log.can_focus = False
        timeline = Vertical(
            Static(_TIMELINE_TITLE, id="timeline-title"), timeline_log, id="timeline")
        chat = VerticalScroll(id="chat")
        # The timeline and chat are display-only. Keep them out of the focus
        # chain so a click (or Tab) can never strand focus on a scroll pane —
        # that used to leave typed / IME (e.g. Chinese) input with no target.
        # Mouse-wheel scrolling still works without focus.
        header.can_focus = timeline.can_focus = chat.can_focus = False
        yield header
        yield timeline
        yield chat
        yield Static("", id="status")
        slash = OptionList(id="slash")          # `/` command autocomplete
        slash.can_focus = False
        slash.display = False
        yield slash
        yield Composer(id="composer")
        yield Footer()

    def on_mount(self):
        for theme in COCKPIT_THEMES:
            self.register_theme(theme)
        self.theme = _theme_name(os.environ.get("CC_COPILOT_THEME", "cockpit"))
        self._sync_rich_palette()
        self.session.refresh()
        self.title = "cc-copilot cockpit"
        self.sub_title = _sub_title(self.session)
        self._chat(self._role(Text(f"cockpit {self.session.store.conv_id[:12]} · "
                                   f"backend {N.backend_name(self.backend).split(' (')[0]}",
                                   "dim"), "role-event"))
        self._rebuild_chat(clear=False)        # repaint any restored prior dialogue
        self._rebuild_timeline()
        self._apply_timeline_height(PREFS.get_int("timeline_height", self.TIMELINE_DEFAULT))
        self._update_header()
        self._announce_since()                 # "N new since last look" on re-entry
        if not N.available(self.backend):
            self.notify("backend unavailable — /model to switch", severity="warning")
        self._update_status()
        composer = self.query_one("#composer", Composer)
        composer.border_title = "› ask the copilot"
        composer.border_subtitle = "Enter send · Ctrl+J newline · / commands · Ctrl+P palette"
        composer.focus()
        self.set_interval(0.12, self._tick_busy, name="busy-spinner")
        self.set_interval(self.poll, self._tick_refresh, name="auto-refresh")
        if self.alerts:
            self.watch_agent()

    def on_unmount(self):
        self._busy = False
        self._watch_stop.set()
        # stamp last-look so the next launch's /since shows what happened while away
        try:
            self.session.mark_lastlook()
        except Exception:
            pass

    def _announce_since(self) -> None:
        """On re-entry, surface how much changed since the human last looked."""
        try:
            sv = self.session.since_summary()
        except Exception:
            sv = None
        if sv is not None and sv.has_changes:
            self._chat(self._role(
                Text(f"⟳ {sv.new_events} new since you last looked — /since to review",
                     style=_PAL["accent"]), "role-event"))

    # ---- resizable activity timeline (the chat fills the remaining space) ----
    def _apply_timeline_height(self, n: int) -> None:
        # never let the timeline eat the screen — leave room for the header,
        # status, composer, footer, and a minimal chat (matters on short terminals
        # and keeps a persisted height sane after moving to a smaller window).
        try:
            room = self.size.height - 10
        except Exception:
            room = self.TIMELINE_MAX
        hi = max(self.TIMELINE_MIN, min(self.TIMELINE_MAX, room))
        n = max(self.TIMELINE_MIN, min(hi, int(n)))
        self._timeline_height = n
        try:
            self.query_one("#timeline").styles.height = n
        except Exception:
            pass

    def action_grow_timeline(self) -> None:
        self._apply_timeline_height(getattr(self, "_timeline_height", self.TIMELINE_DEFAULT) + 1)
        PREFS.set("timeline_height", self._timeline_height)

    def action_shrink_timeline(self) -> None:
        self._apply_timeline_height(getattr(self, "_timeline_height", self.TIMELINE_DEFAULT) - 1)
        PREFS.set("timeline_height", self._timeline_height)

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

    # ---- `/` command autocomplete ----
    @on(TextArea.Changed, "#composer")
    def _slash_update(self, event=None) -> None:
        try:
            comp = self.query_one("#composer", Composer)
            ol = self.query_one("#slash", OptionList)
        except Exception:
            return
        text = comp.text
        single = re.fullmatch(r"/[\w-]*", text)   # one token: '/', no space/newline
        matches = [(c, d) for c, d, _ in _SLASH_CMDS
                   if c.startswith(text.lower())] if single else []
        if not matches:
            self._slash_open = False
            ol.display = False
            return
        ol.clear_options()
        for c, d in matches:
            label = Text(c, style=f"bold {_PAL['primary']}")
            label.append(f"   {d}", style=_PAL["muted"])
            ol.add_option(Option(label, id=c))
        ol.display = True
        self._slash_open = True
        ol.highlighted = 0

    def _slash_move(self, delta) -> None:
        ol = self.query_one("#slash", OptionList)
        if ol.option_count:
            ol.highlighted = max(0, min(ol.option_count - 1, (ol.highlighted or 0) + delta))

    def _slash_hide(self) -> None:
        self._slash_open = False
        try:
            self.query_one("#slash", OptionList).display = False
        except Exception:
            pass

    def _slash_apply(self, cmd) -> None:
        comp = self.query_one("#composer", Composer)
        comp.text = cmd + (" " if cmd in _ARG_CMDS else "")  # arg cmds wait for input
        comp.move_cursor(comp.document.end)
        self._slash_hide()
        comp.focus()

    def _slash_complete(self) -> None:
        ol = self.query_one("#slash", OptionList)
        if ol.option_count:
            self._slash_apply(ol.get_option_at_index(ol.highlighted or 0).id)

    def _slash_accept(self) -> None:
        ol = self.query_one("#slash", OptionList)
        if not ol.option_count:
            return
        cmd = ol.get_option_at_index(ol.highlighted or 0).id
        if cmd in _ARG_CMDS:
            self._slash_apply(cmd)
            return
        comp = self.query_one("#composer", Composer)
        comp.text = ""
        self._slash_hide()
        comp.focus()
        self._meta(cmd)

    @on(OptionList.OptionSelected, "#slash")
    def _slash_pick(self, event) -> None:
        self._slash_apply(event.option.id)

    # ---- command palette ----
    def get_system_commands(self, screen):
        for command in super().get_system_commands(screen):
            if command.title != "Theme":
                yield command
        yield SystemCommand("Observe", "Attention queue + next human decision",
                            self.action_observe)
        yield SystemCommand("Brief", "Evidence-cited recap", self.action_brief)
        yield SystemCommand("Check", "Safety / off-track assessment", self.action_check)
        yield SystemCommand("Diff", "What changed since last turn", self.action_diff)
        yield SystemCommand("Evidence", "Choose one or more agent sessions",
                            self.action_sessions)
        yield SystemCommand("Resume", "Browse resumable cockpit sessions", self.action_history)
        yield SystemCommand("Rewind", "Fork the chat from an earlier message", self.action_rewind)
        yield SystemCommand("Model", "Switch the LLM backend", self.action_model)
        yield SystemCommand("Cockpit Theme", "Switch curated cockpit palette",
                            self.action_theme)
        yield SystemCommand("Refresh", "Re-read the session now", self.action_refresh_now)

    # ---- render helpers ----
    def _role(self, renderable, cls):
        w = Static(renderable, classes=cls)
        return w

    def _chat(self, widget):
        chat = self.query_one("#chat", VerticalScroll)
        chat.mount(widget)
        chat.scroll_end(animate=False)

    def _timeline(self, renderable, cls="role-event", follow=None):
        """Append one line to the activity log. ``follow`` None = tail-follow
        (auto-scroll only when already at the bottom, so scroll-up sticks);
        False = bulk write (caller scrolls once at the end)."""
        rl = self.query_one("#timeline-log", RichLog)
        if follow is None:
            follow = rl.scroll_offset.y >= rl.max_scroll_y - 1
        rl.write(_timeline_gutter(renderable, cls), scroll_end=follow)

    def _land_timeline(self, rl, prev_y, was_bottom, keep_scroll):
        """Land the viewport after a full rebuild. ``keep_scroll`` (a *same-session*
        refresh — poll tick, theme change, manual refresh) holds the reader's
        position: follow the newest line only if they were already at the bottom,
        and keep the horizontal pan either way — scroll_end defaults to x_axis=True,
        which would snap a panned-across long line back to column 0 every tick.
        Otherwise (first build, or an evidence/scope switch into a *different*
        history) land on the newest line and reset the pan — a stale offset would
        open a freshly-selected session scrolled into the middle."""
        if keep_scroll:
            if was_bottom:
                rl.scroll_end(animate=False, x_axis=False)   # follow y, keep x pan
            else:
                rl.scroll_to(y=prev_y, animate=False, force=True)   # x left untouched
        else:
            # evidence switch / first build: land on the newest line at column 0.
            # rl.clear() already reset x→0, and scroll_end lands at (x=0, y=max) —
            # NOT the far right — so a new session shows line starts (timestamps,
            # tool names), never the tail of a long row.
            rl.scroll_end(animate=False)

    def _evidence_sig(self):
        """Identity of *what* the timeline is showing — scope, the session, and the
        multi-session set. A rebuild whose signature is unchanged is a same-session
        refresh (poll tick, theme, /refresh, re-observe, a no-op /scope); a changed
        signature is an evidence switch (/sessions, /use, /here, /scope, /resume)."""
        s = self.session
        return (s.scope, s.path,
                tuple(sorted(str(x) for x in (getattr(s, "scope_sessions", None) or []))))

    def _rebuild_timeline(self):
        rl = self.query_one("#timeline-log", RichLog)
        prev_y = rl.scroll_offset.y
        # "at the bottom" is EXACT here — NOT the append path's `- 1` slack.
        # When the log overflows by a single line (max_scroll_y == 1) that slack
        # would treat a top reader (y == 0) as at-bottom and yank them down.
        was_bottom = prev_y >= rl.max_scroll_y         # capture BEFORE clear
        # Keep the reader's scroll only when the evidence is unchanged; an evidence
        # switch (or the first build) lands on the newest line. Derived, not passed
        # by callers — _refresh_scope_view has both same-evidence and switch callers.
        sig = self._evidence_sig()
        keep_scroll = (sig == self._timeline_sig)
        self._timeline_sig = sig
        snap = _scope_snapshot(self.session)
        title = _scope_activity_title(self.session, snap)
        try:
            self.query_one("#timeline-title", Static).update(title)
        except NoMatches:
            pass
        rl.clear()
        for level, line in O.timeline_lines(
                self.session.path, self.session.st, self.session.scope,
                sessions=self.session.scope_sessions, limit=2):
            self._timeline(_observer_timeline_line(level, line),
                           "role-alert" if level == "alarm"
                           else "role-warn" if level == "warn" else "role-event",
                           follow=False)
        if self.session.scope == SC.SESSION:
            self._timeline(_timeline_status_line(self.session.st), follow=False)
            agent_hex = _agent_hex(_agent_of(self.session))
            for line in _recent_activity_lines(self.session.st, agent_hex=agent_hex):  # the *entire* history
                self._timeline(line, follow=False)
            self._land_timeline(rl, prev_y, was_bottom, keep_scroll)
            return
        if self.session.scope == SC.PROJECT:
            root = _project_cwd(self.session)
            branch, changed = _git_summary(root)
            self._timeline(Text(f"project · {branch} · {changed} git change"
                                f"{'s' if changed != 1 else ''} · {_short_activity(root, 62)}",
                                style=_PAL["muted"]), follow=False)
        self._timeline(_scope_timeline_summary(self.session, snap), follow=False)
        for line in _scoped_recent_activity_lines(snap.get("items", [])):
            self._timeline(line, follow=False)
        self._land_timeline(rl, prev_y, was_bottom, keep_scroll)

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

    def _status(self):
        try:
            return self.query_one("#status", Static)
        except Exception:
            return None

    def _header(self):
        try:
            return self.query_one("#status-header", Static)
        except Exception:
            return None

    def _header_text(self) -> Text:
        root = _project_cwd(self.session)
        project = os.path.basename(root.rstrip(os.sep)) or root
        branch, changed = _git_summary(root)
        snap = _scope_snapshot(self.session)
        st = self.session.st
        a = A.assess(st) if st is not None else None
        t = Text()
        t.append("project ", style=_PAL["muted"])
        t.append(_short_activity(project, 28), style=_PAL["primary"])
        t.append(f" · {branch}", style=_PAL["secondary"])
        t.append(f" · {changed} git change{'s' if changed != 1 else ''}", style=_PAL["muted"])
        t.append(f" · {_short_activity(root, 62)}", style=_PAL["muted"])
        t.append("\n")

        if self.session.scope == SC.SESSION:
            title = _short_activity(_session_title(st), 42) if st is not None else "history-only"
            sid = _sid(st=st, path=self.session.path)
            status = st.status if st is not None else "missing"
            verdict = a.verdict if a is not None else "empty"
            t.append("evidence ", style=_PAL["muted"])
            ag = _agent_of(self.session)
            t.append(f"{ag} session", style=_agent_hex(ag))
            t.append(f" · {title} · {sid}", style=_PAL["text"])
            t.append(f" · {status}", style="bold")
            t.append(f" · {verdict}", style=_VERDICT_HEX.get(verdict, _PAL["muted"]))
            t.append("\n")
            ev = st.tr.raw_lines if st is not None else 0
            idle = _dur(st.idle_seconds) if st is not None else "?"
            t.append(f"activity current session · idle {idle} · {ev} ev", style=_PAL["muted"])
            return t

        label = self.session.scope_label()
        selected = _selection_label(self.session, snap)
        health = " · ".join(_health_bits(snap.get("items", [])))
        t.append("evidence ", style=_PAL["muted"])
        t.append(label, style=_PAL["accent"])
        t.append(f" · {selected} sessions", style=_PAL["text"])
        if snap.get("error"):
            t.append(f" · {snap['error']}", style=_PAL["warning"])
        else:
            t.append(f" · {health}", style=_PAL["muted"])
        mix = _agent_mix(snap.get("items", []))
        if mix:
            t.append(f" · {mix}", style=_PAL["accent"])
        t.append("\n")

        t.append(f"cockpit {self.session.store.conv_id[:12]} · project context always on",
                 style=_PAL["muted"])
        return t

    def _update_header(self):
        header = self._header()
        if header is not None:
            header.update(self._header_text())

    def _refresh_scope_view(self):
        self._rebuild_timeline()
        self._update_header()
        self._update_status()

    def _evidence_label(self) -> str:
        """What the cockpit is watching, named by agent for a single session.

        Distinguishes the watched agent from the copilot *backend* shown beside
        it — both can now be 'codex'. Cheap (no sibling parse); the header
        carries the multi-session agent mix.
        """
        if getattr(self.session, "scope", SC.SESSION) == SC.SESSION:
            return f"{_agent_of(self.session)} session"
        return self.session.scope_label()

    def _watch_hex(self) -> str:
        """Color for the status-line `watching …` span: the watched agent's brand
        hue for a single session, the copilot accent for a multi-agent scope (no
        single brand)."""
        if getattr(self.session, "scope", SC.SESSION) == SC.SESSION:
            return _agent_hex(_agent_of(self.session))
        return _PAL["accent"]

    def on_resize(self, event) -> None:
        # The status bar reflows to the width: a single dense line when it fits,
        # else stacked rows so a narrow sidebar still shows every field. Re-render
        # on resize (cheap — a handful of Text appends; the poll timer is 2s).
        self._update_status()

    def _update_status(self):
        status = self._status()
        if status is None:
            return
        try:
            w = max(8, self.size.width - 2)        # content width (padding: 0 1)
        except Exception:
            w = 90
        status.update(self._status_text(w))

    def _status_text(self, w: int) -> Text:
        """The status bar, reflowed to width ``w``. Every field is an atomic styled
        span; the renderer picks the widest layout that fits so a sidebar keeps all
        of them (the inline line → a 2-row split → fully stacked rows)."""
        st = self.session.st
        be = N.backend_name(self.backend).split(" (")[0]
        copilot = ("copilot " + be + (":" + self.model if self.model else ""),
                   _PAL["secondary"])
        watch_lbl, watch_sty = self._evidence_label(), self._watch_hex()

        if st is None:                              # history-only (transcript gone)
            hist = (" ⌁ history-only ", "bold")
            gone = (" transcript gone ", f"bold {_PAL['bg']} on {_PAL['warning']}")
            inline = _assemble([hist, ("  ", None), gone, ("  ", None), copilot,
                                ("   ", None), ("watching " + watch_lbl, watch_sty)])
            if inline.cell_len <= w:
                return inline
            return _assemble([hist, ("  ", None), gone, ("\n", None), copilot,
                              ("\n", None), ("↳ " + watch_lbl, watch_sty)])

        a = A.assess(st)
        chip = (f" {_STATUS_GLYPH.get(st.status, '·')} {st.status} ", "bold")
        badge = (f" {a.verdict.upper()} ",
                 f"bold {_PAL['bg']} on {_VERDICT_HEX.get(a.verdict, _PAL['muted'])}")
        idle = f"idle {_dur(st.idle_seconds)} · {st.tr.raw_lines} ev"
        # HUD: reuse the canonical EC formatters as the single source of truth, and
        # split their " · " output so the stacked layout can wrap on field bounds.
        if self._busy:
            ans = (EC.format_answering(self._ctx_stats, self._out_tokens)
                   if self._ctx_stats is not None else "")
            hud_str = _busy_indicator(self._busy_frame) + ((" · " + ans) if ans else "")
            hud_parts, hud_sty = (ans.split(" · ") if ans else []), _PAL["accent"]
        elif self._ctx_stats is not None:
            hud_str = EC.format_hud(self._ctx_stats, self._out_tokens)
            hud_parts, hud_sty = hud_str.split(" · "), _PAL["muted"]
        else:
            hud_str, hud_parts, hud_sty = "", [], _PAL["muted"]

        watch_seg = ("watching " + watch_lbl, watch_sty)
        idle_seg = (idle, _PAL["muted"])

        # 1) inline — today's single dense line, when it fits.
        segs = [chip, ("  ", None), badge, ("  ", None), copilot,
                ("   ", None), watch_seg, ("   ", None), idle_seg]
        if hud_str:
            segs += [("   ", None), (hud_str, hud_sty)]
        inline = _assemble(segs)
        if inline.cell_len <= w:
            return inline

        # 2) medium — identity row + an activity/HUD row, when the identity fits.
        id_row = _assemble([chip, ("  ", None), badge, ("  ", None), copilot,
                            ("   ", None), watch_seg])
        if id_row.cell_len <= w:
            t = Text()
            t.append_text(id_row)
            t.append("\n")
            t.append(idle, style=_PAL["muted"])
            if hud_str:
                t.append(" · ")
                t.append(hud_str, style=hud_sty)   # soft-wraps on " · " (text-wrap)
            return t

        # 3) narrow stacked — author-controlled rows; complete and clean to ~30 cols.
        t = Text()
        pin = _assemble([chip, ("  ", None), badge])
        if pin.cell_len <= w:                       # PIN: status + verdict share row 1
            t.append_text(pin)
        else:                                       # brutal width: badge to its own row
            t.append(*chip)
            t.append("\n")
            t.append(*badge)
        t.append("\n")
        t.append(*copilot)
        t.append("\n")
        t.append("↳ " + watch_lbl, style=watch_sty)
        t.append(" · " + idle, style=_PAL["muted"])
        if self._busy:
            head = " · ".join(hud_parts[:2])
            t.append("\n")
            t.append(_busy_indicator(self._busy_frame) + ((" · " + head) if head else ""),
                     style=hud_sty)
            if hud_parts[2:]:
                t.append("\n")
                t.append(" · ".join(hud_parts[2:]), style=hud_sty)
        elif hud_parts:
            trimmed = "trimmed" in hud_parts
            core = [p for p in hud_parts if p != "trimmed"]
            t.append("\n")
            t.append(" · ".join(core[:3]), style=hud_sty)        # ctx · out · raw
            if core[3:]:
                t.append("\n")
                t.append(" · ".join(core[3:]), style=hud_sty)    # project · chat · memory · index
            if trimmed:
                t.append("\n")
                t.append("trimmed", style=_PAL["warning"])
        return t

    def _tick_busy(self) -> None:
        if not self._busy:
            return
        self._busy_frame = (self._busy_frame + 1) % len(_BUSY_FRAMES)
        self._update_status()

    def _tick_refresh(self) -> None:
        # The watcher owns rich per-event deltas when alerts are enabled. This
        # periodic pass keeps the wider scope/project surfaces current, and also
        # keeps the cockpit reactive when alert toasts are disabled.
        changed = self.session.refresh() if not self.alerts else False
        if changed or self.session.scope != SC.SESSION:
            self._rebuild_timeline()
        self._update_header()
        self._update_status()

    # ---- input ----
    @on(Composer.Submitted)
    def _on_submit(self, event: Composer.Submitted):
        self._slash_hide()
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
        ctx = self.session.answer_context(text, history=list(self.session.history))
        self._ctx_stats = ctx.stats
        self._out_tokens = 0
        self.session.last_context_stats = ctx.stats
        self.session.last_output_tokens = 0
        self._busy = True
        self._busy_frame = 0
        self._update_status()
        # Capture the originating conversation (store + state). If the user
        # switches sessions before the backend returns, the answer is recorded
        # against the session it was ASKED in — not whatever is current now.
        self._answer(text, ctx.text, self.session.st, list(self.session.history),
                     self.session.store)

    @work(thread=True)
    def _answer(self, text, brief_text, st, history, store):
        try:
            ans = N.chat_brief(brief_text, [], text, model=self.model, backend=self.backend)
            ok = True
        except Exception as e:
            ans, ok = f"# error: {e}", False
        self.call_from_thread(self._answer_done, text, ans, ok, st, store)

    def _answer_done(self, text, ans, ok, st, store):
        self._busy = False
        self._busy_frame = 0
        self._out_tokens = EC.estimate_tokens(ans) if ok else 0
        self.session.last_output_tokens = self._out_tokens
        same = store is self.session.store     # still on the originating conversation?
        if ok:
            # the cockpit's single durable write-site (the REPL has its own in
            # ChatSession.answer); _answer runs on a worker thread, hence here.
            # Persist to the originating store, even if the user has switched away.
            store.scope = self.session.scope
            store.scope_sessions = list(self.session.scope_sessions)
            store.record_turn(text, ans, st=st, backend=self.backend, model=self.model)
            if same:
                self.session.history.append(("user", text))
                self.session.history.append(("assistant", ans))
                self._chat(Markdown(ans, classes="role-assistant"))
            # if switched away: the turn is safe on disk and reappears on return,
            # so we don't render it into the now-current (different) conversation.
        elif same:
            self._chat(self._role(Text(ans, style=_PAL["error"]), "role-alert"))
        self._update_header()
        self._update_status()

    # ---- /since: deterministic delta, narrated into a grounded recap ----
    def _since_cmd(self, arg: str):
        """Recap by default (grounded in the cited delta), with the deterministic
        evidence beneath it; instant deterministic view for `--raw`, no backend,
        nothing-new, or while busy. The model call runs off the UI thread."""
        title = (f"/since {arg}").strip()
        res = self.session._since_view(arg)
        if isinstance(res, str):                 # edge-case message (no mark, etc.)
            self._collapsible(title, res)
            return
        view, raw, commit = res
        if raw or view.nothing_new or not N.available(self.backend) or self._busy:
            self._collapsible(title, view.text)  # deterministic, instant
            commit()                             # shown → advance the marker
            return
        self._busy = True
        self._busy_frame = 0
        self._chat(self._role(
            Text(f"🛰  recapping {title} — grounded in the evidence…",
                 style=_PAL["muted"]), "role-event"))
        self._update_status()
        # capture both what this recap is ABOUT and the conversation it was asked
        # in: an evidence switch (/use, /sessions, /here) changes the signature,
        # while /new or /resume on the same transcript swaps the conversation store
        # but not the signature. Either makes the result stale, so we drop it (and
        # leave the last-look marker un-consumed) rather than mis-render it.
        self._since_recap(title, view, (self._evidence_sig(), self.session.store), commit)

    @work(thread=True)
    def _since_recap(self, title, view, origin, commit):
        try:
            recap = N.recap_since(view.text, model=self.model, backend=self.backend)
            out = self.session._compose_since(recap, view)
        except Exception as e:
            out = view.text + f"\n\n> _recap unavailable ({e}); evidence shown above._"
        self.call_from_thread(self._since_done, title, out, origin, commit)

    def _since_done(self, title, out, origin, commit):
        self._busy = False
        self._busy_frame = 0
        sig, store = origin
        if self._evidence_sig() == sig and self.session.store is store:
            self._collapsible(title, out)
            commit()                             # rendered → advance the marker
        else:
            self.notify(f"dropped {title} recap — you switched while it ran",
                        severity="warning")      # not committed → delta survives
        self._update_status()

    # ---- background watcher ----
    def _reset_watch_baseline(self):
        self._watch_path = self.session.path
        self._watch_size = self.session.last_size
        self._watch_state = self.session.st

    @work(thread=True, exclusive=True, group="watch")
    def watch_agent(self):
        self._reset_watch_baseline()
        while not self._watch_stop.wait(self.poll):
            path = self.session.path
            if path != self._watch_path:
                self._reset_watch_baseline()
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size == self._watch_size:
                continue
            self._watch_size = size
            try:
                st = S.build(SRC.parse(path))
            except Exception:
                continue
            d = S.diff(self._watch_state, st)
            self._watch_state = st
            self.call_from_thread(self._on_watch, st, d)

    def _on_watch(self, st, d):
        self.session.st = st
        self._update_header()
        self._update_status()
        if d.new_events or d.status_from != d.status_to or d.verdict_from != d.verdict_to:
            self._timeline(_timeline_delta_line(st, d))
            line = (_activity_line(st.last_record, _agent_hex(_agent_of(self.session)))
                    if st.last_record is not None else None)
            if line is not None:
                self._timeline(line)
        for fc in d.new_changed[:4]:
            self._timeline(Text(f"✎ {os.path.basename(fc.path)}  ({fc.total} edit/write)  [L{fc.last_line}]"))
        for f in d.new_failures[:4]:
            self._timeline(Text(f"✗ {f.tool} failed  [L{f.line}]", style=_PAL["error"]), "role-alert")
        msg = _fmt_alert(d)
        if msg:
            sev = "error" if "INTERVENE" in msg or "STALLED" in msg else "warning"
            self.notify(msg, severity=sev, title="observed session")

    # ---- meta commands (typed `/…` still works) ----
    def _meta(self, cmd):
        low = cmd.strip().lower()
        if low in ("/quit", "/exit", "/q"):
            self.exit(); return
        if low in ("/help", "/?"):
            self._collapsible("/help", _HELP_TEXT); return
        if low == "/observe":
            self.action_observe(); return
        if low == "/brief":
            self.action_brief(); return
        if low == "/check":
            self.action_check(); return
        if low == "/diff":
            self.action_diff(); return
        if low in ("/sessions", "/session"):
            self.action_sessions(); return
        if low == "/here":
            if not self.session.switch_to_here():
                self.notify("no current session detected (CLAUDE_CODE_SESSION_ID unset)",
                            severity="warning")
                return
            self._reset_watch_baseline()
            self._refresh_scope_view()
            self.notify("now observing your live session", severity="information")
            return
        if low == "/resume" or low.startswith("/resume") or low == "/history" or low.startswith("/history"):
            self.action_history(); return
        if low == "/new" or low == "/new-cockpit":
            out = self.session.new_cockpit()
            self._rebuild_chat()
            self._refresh_scope_view()
            self.notify(str(out).splitlines()[0], severity="information")
            return
        if low == "/theme" or low.startswith("/theme "):
            arg = cmd.strip()[6:].strip().lower()
            if arg:
                self._set_theme(arg)
            else:
                self.action_theme()
            return
        if low == "/scope" or low.startswith("/scope "):
            arg = cmd.strip()[6:].strip()
            if arg:
                out = self.session.meta(cmd)
                self.notify(str(out).splitlines()[0])
                self._refresh_scope_view()
            else:
                self.action_sessions()
            return
        if low == "/model" or low.startswith("/model "):
            arg = cmd.strip()[6:].strip()
            if arg:
                self._set_backend(arg)
            else:
                self.action_model()
            return
        if low == "/refresh":
            self.action_refresh_now(); return
        if low in ("/clear", "/cls"):
            self.action_clear_chat(); return
        if low == "/forget":
            self.action_forget(); return
        if low == "/rewind":
            self.action_rewind(); return
        if low.startswith("/use"):
            out = self.session.meta(cmd)
            self.notify(str(out).splitlines()[0])
            self._reset_watch_baseline()
            self._refresh_scope_view(); return
        if low == "/since" or low.startswith("/since "):
            if self._no_live():
                return
            arg = cmd.strip()[6:].strip()
            self._since_cmd(arg)
            return
        if low == "/handoff" or low.startswith("/handoff "):
            if self._no_live():
                return
            arg = cmd.strip()[8:].strip()
            out = self.session._handoff(arg)
            if arg:                              # wrote to a file → just confirm
                self.notify(str(out).splitlines()[0], severity="information")
            else:
                self._collapsible("/handoff", out)
            return
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
        self._collapsible(f"/brief — {self.session.scope_label()}",
                          self.session.evidence().text)
        self._update_status()

    def action_observe(self):
        self.session.refresh()
        if self._no_live():
            return
        try:
            body = O.render(self.session.path, self.session.st, self.session.scope,
                            sessions=self.session.scope_sessions)
        except ValueError as e:
            self.notify(str(e), severity="warning")
            return
        self._collapsible(f"/observe — {self.session.scope_label()}", body)
        self._refresh_scope_view()

    def action_check(self):
        self.session.refresh()
        if self._no_live():
            return
        body = (_check_text(self.session.st) if self.session.scope == SC.SESSION
                else self.session.evidence().text)
        self._collapsible(f"/check — {self.session.scope_label()}", body)
        self._update_status()

    def action_diff(self):
        self.session.refresh()
        if self._no_live():
            return
        self._collapsible("/diff — changes since last turn",
                          self._diff_renderable(S.diff(self.session.prev, self.session.st)))

    @work
    async def action_sessions(self):
        refs = self.session.sibling_refs()
        opts = [(_session_picker_label(r, self.session.path), r.session_id) for r in refs]
        selected = _session_selection_ids(self.session, refs)
        chosen = await self.push_screen_wait(
            MultiPicker("choose evidence sessions", opts, selected=selected))
        if chosen is None:
            return
        try:
            msg = _apply_session_selection(self.session, refs, chosen)
        except ValueError as e:
            self.notify(str(e), severity="warning")
            return
        self._reset_watch_baseline()
        self.sub_title = _sub_title(self.session)
        self._refresh_scope_view()
        self.notify(msg, severity="information")

    @work
    async def action_history(self):
        if not self.session.store.enabled:
            self.notify("resume is off (--no-persist or [history] enabled=false)",
                        severity="warning")
            return
        headers = ST.list_conversations(getattr(self.session, "cwd", None) or None)
        if not headers:
            self.notify("no resumable cockpit sessions yet"); return
        opts = []
        for h in headers:
            gone = "  (gone)" if not h.transcript_present else ""
            proj = os.path.basename(h.cwd) or "?"
            opts.append((f"{(h.title or '(untitled)')[:32]:<32} · {h.turns:>2}t · "
                         f"{h.ago()} · {proj}{gone}", h))
        chosen = await self.push_screen_wait(
            Picker("resume a cockpit session", opts))
        if chosen:
            live = self.session.attach_conv(chosen)
            self._rebuild_chat()
            self._reset_watch_baseline()
            self.sub_title = chosen.conv_id[:8]
            self._refresh_scope_view()
            self.notify(("→ " if live else "history-only → ") + chosen.conv_id[:8],
                        severity="information" if live else "warning")

    @work
    async def action_rewind(self):
        hist = self.session.history
        qs = [t for r, t in hist if r == "user"]
        if not qs:
            self.notify("nothing to rewind — no questions yet"); return
        opts = []
        for k, q in enumerate(qs):
            ans = hist[2 * k + 1][1] if 2 * k + 1 < len(hist) else ""
            label = f"#{k + 1}  {q[:46]}" + (f"   → {ans[:26]}" if ans else "")
            opts.append((label, k))
        opts.reverse()                                  # newest first
        chosen = await self.push_screen_wait(
            Picker("rewind — fork from an earlier message (re-asks it)", opts))
        if chosen is not None:
            self._rewind_to(chosen)

    def _rewind_to(self, k):
        # keep turns [0, k); drop message k and everything after; re-load it for editing
        hist = self.session.history
        qs = [t for r, t in hist if r == "user"]
        if not (0 <= k < len(qs)):
            return
        question = qs[k]
        self.session.history = hist[:2 * k]             # turns are user/assistant pairs
        self.session.store.truncate(k)                  # persist the fork
        self._rebuild_chat()
        comp = self.query_one("#composer", Composer)
        comp.text = question
        comp.move_cursor(comp.document.end)
        comp.focus()
        self.notify(f"rewound to message #{k + 1} — edit & Enter to re-ask")

    @work
    async def action_model(self):
        opts = [(f"{name}{'  ✓' if be.available() else '  · ' + be.reason()}", name)
                for name, be in sorted(BK.registry().items())]
        chosen = await self.push_screen_wait(Picker("switch backend", opts))
        if chosen:
            self._set_backend(chosen)

    def action_change_theme(self) -> None:
        self.action_theme()

    @work
    async def action_theme(self):
        opts = []
        for name in COCKPIT_THEME_NAMES:
            spec = COCKPIT_THEME_SPECS[name]
            mark = "  ✓" if name == self.theme else ""
            opts.append((f"{spec['label']:<10} · {spec['description']}{mark}", name))
        chosen = await self.push_screen_wait(Picker("switch cockpit theme", opts))
        if chosen:
            self._set_theme(chosen)

    def _sync_rich_palette(self) -> None:
        global _PAL, _VERDICT_HEX
        _PAL = _rich_palette(self.theme)
        _VERDICT_HEX = _verdict_palette(self.theme)

    def _set_theme(self, name: str) -> None:
        if name not in COCKPIT_THEME_SPECS:
            self.notify(f"unknown theme {name!r}", severity="warning")
            return
        self.theme = name
        self._sync_rich_palette()
        self._rebuild_timeline()
        self._update_header()
        self._update_status()
        label = COCKPIT_THEME_SPECS[name]["label"]
        self.notify(f"theme → {label}", severity="information")

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
        self._rebuild_timeline()
        self._update_header()
        self._update_status()
        self.notify("history-only — no live session" if self.session.st is None
                    else "refreshed")

    def action_clear_chat(self):
        # Visual only — tidies the pane, keeps the saved history (so it returns
        # on switch-back / relaunch). Use /forget to actually delete it.
        self.query_one("#chat", VerticalScroll).remove_children()
        self.notify("view cleared (saved history kept — /forget to delete it)")

    def action_forget(self):
        # Delete THIS conversation's saved history from disk + clear the view.
        store = self.session.store
        if not store.enabled:
            self.notify("history is off — nothing saved to forget", severity="warning")
            return
        store.delete()
        self.session.history = []
        chat = self.query_one("#chat", VerticalScroll)
        chat.remove_children()
        chat.mount(self._role(
            Text("(forgot this conversation's saved history)", "dim"), "role-event"))
        self.notify("forgot this conversation's saved history", severity="warning")


# small adapters so the cockpit doesn't reach into private chat internals
def B_render(st):
    from .brief import render
    return render(st)


def _check_text(st):
    from .brief import render_check
    return render_check(st)


def run(session, poll=2, alerts=True):
    Cockpit(session, poll=poll, alerts=alerts).run()
