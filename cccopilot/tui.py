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
import random
import re
import subprocess
import threading
import time

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
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.css.query import NoMatches
    from textual.message import Message
    from textual.screen import ModalScreen
    from textual.theme import Theme
    from textual.widgets import (Button, Footer, Input, Markdown,
                                 OptionList, RadioButton, RadioSet, RichLog,
                                 Static, TextArea)
    from textual.widgets.option_list import Option
    from rich.text import Text
    from rich.table import Table
    from rich.console import Group
    from rich.cells import cell_len as _cell_len
except ImportError:
    raise SystemExit(
        "the cockpit TUI needs Textual. Run:  cc-copilot setup\n"
        "(or: pip install 'cc-copilot[tui]')")

from . import (sources as SRC, state as S, assess as A, narrate as N,
               backends as BK, store as ST, scope as SC, locate as LOC,
               observe as O, context as EC, prefs as PREFS, onboard as OB,
               models as MODELS)
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
_MSG_QUEUE_MAX = 8                # chat messages that can wait behind a live answer
_TIMELINE_TITLE = "session activity"

# Per-agent identity hues — the *watched* agent's brand color (Claude's
# apricot-rust, Codex's blue), applied to agent-identity spans: the timeline
# `agent` label and the header's "<agent> session". These are theme-independent
# (an agent's brand is the agent's brand). Unknown agents fall back to the
# copilot's own accent so its chrome color shows through instead of a stray hue.
_AGENT_HEX = {"claude": "#cb7d5b", "codex": "#347ff2"}


def _agent_hex(agent: str) -> str:
    return _AGENT_HEX.get((agent or "").strip().lower(), _PAL["accent"])


def _backend_choice_hex(name: str) -> str:
    choice = OB.choice_for_or_none(name)
    return choice.brand_hex if choice else ""

_HELP_TEXT = (
    "ask a question (newline: Ctrl+J · send: Enter · history: ↑/↓ · clear: Esc)\n"
    "type `/` for command suggestions (Enter accepts, Tab completes; palette: Ctrl+P):\n"
    "  /observe /brief /check  attention · recap · safety (LLM-free)\n"
    "  /now [steer]            recommend the next step (e.g. /now in spanish; LLM)\n"
    "  /since [30m|1d] [--raw] [steer]  recap since you last looked (--raw = cited delta)\n"
    "  /handoff [file]         shareable Markdown handoff\n"
    "  /diff                   changes since last turn\n"
    "  /status                 fleet board — every session, neediest first\n"
    "  /sessions  /here         choose evidence session(s) · watch your own live one\n"
    "  /target                 show the current cockpit target (id · evidence · scope)\n"
    "  /resume                 resume a cockpit session\n"
    "  /new                    start a new cockpit session\n"
    "  /theme                  switch cockpit palette\n"
    "  /rewind                 fork the chat from an earlier message (Esc Esc on empty)\n"
    "  /model [name]           switch backend                     (Ctrl+T)\n"
    "  /init                   reopen the model picker (Claude / Codex / API key)\n"
    "  /stop                   interrupt the in-flight answer, keep the cockpit  (Ctrl+Z)\n"
    "  /use <n|id>  /refresh   /clear   /forget   /quit\n"
    "keys: Ctrl+R refresh · Ctrl+L clear · Ctrl+Y copy · Ctrl+Z stop · Shift+↑/↓ resize · Ctrl+C quit\n"
    "      Empty input: ←/→ jumps between prior prompts in chat.\n"
    "      Esc clears input; Esc twice on empty opens rewind.\n"
    "copy: drag to select, then Ctrl+Y — clean text to your clipboard (works over tmux/SSH).\n"
    "      Ctrl+C quits. ⌘C does your terminal's own copy of the terminal's selection.")

# Slash commands, shown in the `/` autocomplete (name, one-line help, takes-arg).
# Only the primary spelling of each command is listed. Short convenience aliases
# (/q /? /cls /exit /onboard /new-cockpit) AND the power-user evidence commands
# /scope and /history are dispatched in _meta() but intentionally kept OUT of
# autocomplete — the visual /sessions picker is the blessed way to scope in the
# cockpit (see test_deprecated_control_shortcuts_*). Keep it scannable.
_SLASH_CMDS = [
    ("/observe", "attention queue + next human decision", False),
    ("/now", "recommend the next step — add a steer like 'in spanish' (LLM; deterministic fallback)", False),
    ("/since", "recap since you last looked (30m / 2h / 1d; --raw = cited delta; trailing text steers it)", True),
    ("/handoff", "shareable Markdown handoff (brief + what changed)", True),
    ("/brief", "evidence-cited recap (LLM-free)", False),
    ("/check", "safety / off-track verdict (LLM-free)", False),
    ("/diff", "what changed since your last turn", False),
    ("/status", "fleet board — every session in this project, neediest first", False),
    ("/sessions", "choose one or more evidence sessions", False),
    ("/use", "change evidence session by number / id", True),
    ("/here", "observe your own current (live) session", False),
    ("/target", "current cockpit target (id, evidence session, scope)", False),
    ("/resume", "browse & resume cockpit sessions", False),
    ("/new", "start a new independent cockpit session (alias: /new-cockpit)", False),
    ("/theme", "switch cockpit palette", False),
    ("/model", "switch the LLM backend", True),
    ("/init", "reopen the model picker (choose Claude/Codex/an API key)", False),
    ("/rewind", "fork from an earlier message (or Esc Esc on empty input)", False),
    ("/refresh", "re-read the observed session now", False),
    ("/stop", "interrupt the in-flight answer (Ctrl+Z) — keeps the cockpit running", False),
    ("/forget", "delete THIS cockpit session's saved resume state", False),
    ("/clear", "clear the chat view (keeps saved history)", False),
    ("/help", "show help", False),
    ("/quit", "exit the cockpit", False),
]
_ARG_CMDS = {c for c, _, takes in _SLASH_CMDS if takes}

# Rotating feature tips shown subtly above the composer (see _rotate_tip). They
# carry the discoverability the slimmed-down footer no longer shows — ordered
# from "most useful when you just got back" down to niche keys. Each is one line,
# <=64 chars so it survives a narrow sidebar. Curated from the core feature set.
_TIPS = [
    "/since recaps what the agent did while you were away",
    "Re-entry greets you: N new since you last looked",
    "Every recap line cites a transcript line [L#] — never guessed",
    "/check tells you if it's safe to continue — off-track signals",
    "/handoff writes a shareable Markdown brief of what changed",
    "/observe surfaces the next human decision waiting on you",
    "/brief recaps with sources, no LLM, no guessing",
    "/diff shows what changed since your last turn",
    "Read-only: the cockpit never writes to the agent or transcript",
    "/sessions picks which session(s) the cockpit watches",
    "/use <n|id> switches the watched session by number or id",
    "/here watches your OWN current live session",
    "/scope multi or project widens the evidence across sessions",
    "One cockpit watches Claude AND Codex at once, by project",
    "/model switches the backend — Claude, Codex or an API",
    "/init reopens the model picker; /theme switches the palette",
    "/resume reopens a past cockpit · /new starts fresh; Q&A stays",
    "Empty input: Left/Right jumps between prior prompts",
    "Shift+Up/Down resizes the activity timeline",
    "/clear or Ctrl+L wipes the view, saved history stays",
    "Drag to select text, then Ctrl+Y copies it to the clipboard",
]


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


def _pack_rows(parts, w: int, sep: str = " · ") -> list:
    """Greedy-pack ``sep``-joined parts into rows whose display width is <= ``w``,
    so each rendered row is ONE visual line (no surprise soft-wrap that would push
    a stacked status past its height cap and clip a field). A part wider than ``w``
    gets its own row — the only place a soft-wrap can still happen, and only at
    pathological widths."""
    rows, cur = [], ""
    for p in parts:
        cand = p if not cur else cur + sep + p
        if cur and _cell_len(cand) > w:
            rows.append(cur)
            cur = p
        else:
            cur = cand
    if cur:
        rows.append(cur)
    return rows


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

    def _cursor_row(self) -> int:
        loc = getattr(self, "cursor_location", None)
        if isinstance(loc, tuple) and loc:
            return int(loc[0] or 0)
        row = getattr(loc, "row", None)
        try:
            return int(row or 0)
        except (TypeError, ValueError):
            return 0

    def _replace_text(self, text: str) -> None:
        self.text = text or ""
        try:
            self.move_cursor(self.document.end)
        except Exception:
            pass

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

        if event.key in ("up", "down"):
            lines = (self.text or "").splitlines() or [""]
            row = self._cursor_row()
            at_history_edge = ("\n" not in self.text
                               or (event.key == "up" and row <= 0)
                               or (event.key == "down" and row >= len(lines) - 1))
            if at_history_edge:
                fn = getattr(app, "_prompt_history_prev" if event.key == "up"
                             else "_prompt_history_next", None)
                replacement = fn(self.text) if callable(fn) else None
                if replacement is not None:
                    event.prevent_default()
                    event.stop()
                    self._replace_text(replacement)
                    return

        if event.key in ("left", "right") and not self.text:
            fn = getattr(app, "_jump_chat_prompt", None)
            if callable(fn) and fn(-1 if event.key == "left" else 1):
                event.prevent_default()
                event.stop()
                return

        # Esc clears the current draft. On an already-empty composer, a quick
        # second Esc opens rewind; the first tap only primes it.
        if event.key == "escape":
            event.prevent_default(); event.stop()
            if self.text:
                self.text = ""
                reset = getattr(app, "_reset_prompt_history_nav", None)
                if callable(reset):
                    reset()
                cancel = getattr(app, "_cancel_rewind_esc", None)
                if callable(cancel):
                    cancel()
                return
            empty = getattr(app, "_empty_composer_escape", None)
            if callable(empty):
                empty()
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
        self._options = options            # [(label/renderable, value), …]
        self._by_id = {str(i): v for i, (l, v) in enumerate(options)}

    @staticmethod
    def _plain_label(label) -> str:
        return label.plain if isinstance(label, Text) else str(label)

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
            if q in self._plain_label(label).lower():
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


# ── first-run onboarding ────────────────────────────────────────────────────
class WelcomeScreen(ModalScreen):
    """Shown once, on the very first cockpit launch (no ~/.cc-copilot.toml yet).
    A brand-colored backend picker: Claude / Codex use the agent's own login (no
    key); the API providers take a key inline. Writes the config and applies the
    choice to the live cockpit, then dismisses with ``(name, choice)`` — or
    ``("skip", None)`` on Skip/Esc (which still writes a config so we stop
    asking). The heavy lifting lives in :mod:`cccopilot.onboard`, shared with the
    terminal ``cc-copilot init`` wizard."""

    BINDINGS = [Binding("escape", "skip", "skip")]

    def __init__(self, detected):
        super().__init__()
        self._detected = detected                 # list[onboard.Detected]
        self._default = next((i for i, d in enumerate(detected)
                              if d.choice.kind == "cli" and d.ready), 0)

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome"):
            yield Static("cc-copilot · welcome", id="welcome-title")
            yield Static(
                "Pick the model that powers recaps, chat & since summaries.\n"
                "The deterministic core (brief / check / observe) needs no model.",
                id="welcome-intro")
            with RadioSet(id="welcome-choices"):
                for i, d in enumerate(self._detected):
                    yield RadioButton(self._row_label(d), value=(i == self._default))
            yield Input(placeholder="API key — paste & Enter (API providers only)",
                        password=True, id="welcome-key")
            yield Static("", id="welcome-hint")
            with Horizontal(id="welcome-actions"):
                yield Button("Save & enter cockpit", id="welcome-save", variant="primary")
                yield Button("Skip for now", id="welcome-skip")

    def _row_label(self, d) -> Text:
        c = d.choice
        hue = c.brand_hex or _PAL["accent"]
        t = Text()
        t.append(f"{c.label:<13}", style=f"bold {hue}")
        mark = "✓" if d.ready else "·"
        t.append(f"{mark} {d.status}", style=_PAL["text"] if d.ready else _PAL["muted"])
        return t

    def on_mount(self) -> None:
        self.query_one("#welcome-choices", RadioSet).focus()
        self._sync_chrome()

    def _selected_index(self) -> int:
        idx = self.query_one("#welcome-choices", RadioSet).pressed_index
        return idx if idx is not None and idx >= 0 else self._default

    def _sync_chrome(self) -> None:
        """Show the key field only for API providers; explain the highlighted row."""
        c = self._detected[self._selected_index()].choice
        self.query_one("#welcome-key", Input).display = (c.kind == "api")
        hint = self.query_one("#welcome-hint", Static)
        if c.kind == "api":
            have = os.environ.get(c.key_env)
            msg = (f"{c.label}: key already detected — leave the field blank to use it."
                   if have else f"{c.label}: paste your {c.key_env} above.")
            if c.default_model:
                msg += f"  (model: {c.default_model})"
            hint.update(Text(msg, style=_PAL["muted"]))
        elif c.kind == "cli":
            hint.update(Text(c.blurb, style=_PAL["muted"]))
        else:
            hint.update(Text("No model now — recaps show the cited evidence only. "
                             "Choose one later with /init.", style=_PAL["muted"]))

    @on(RadioSet.Changed, "#welcome-choices")
    def _on_change(self, event) -> None:
        self._sync_chrome()

    @on(Input.Submitted, "#welcome-key")
    def _on_key_submit(self, event) -> None:
        self._save()

    @on(Button.Pressed, "#welcome-save")
    def _on_save(self, event) -> None:
        self._save()

    @on(Button.Pressed, "#welcome-skip")
    def _on_skip(self, event) -> None:
        self.action_skip()

    def _save(self) -> None:
        c = self._detected[self._selected_index()].choice
        key = self.query_one("#welcome-key", Input).value.strip()
        model = c.default_model if c.kind == "api" else ""
        if c.kind == "api" and not key and not os.environ.get(c.key_env):
            self.query_one("#welcome-hint", Static).update(
                Text(f"{c.label} needs an API key ({c.key_env}) — paste it, "
                     f"or pick a CLI option / Skip.", style=_PAL["warning"]))
            self.query_one("#welcome-key", Input).focus()
            return
        name = c.name or "skip"
        try:
            OB.write_choice(name, model=model, key_value=key)
            OB.apply_to_env(name, model=model, key_value=key)
        except OSError as e:
            self.query_one("#welcome-hint", Static).update(
                Text(f"could not save config: {e}", style=_PAL["error"]))
            return
        self.dismiss((name, c))

    def action_skip(self) -> None:
        try:
            OB.write_choice("skip")           # still writes a config: stop asking
        except OSError:
            pass
        self.dismiss(("skip", None))


class KeyPrompt(ModalScreen):
    """Capture an API key inline when the quick `/model` switch lands on a
    provider that needs one. The backend picker can't carry a key, so without
    this the cockpit switches to (say) DeepSeek silently and every chat then
    fails at call time with "set DEEPSEEK_API_KEY". A focused single-provider
    cousin of :class:`WelcomeScreen`'s key field. Dismisses with the entered key
    string on Save, or None if cancelled (keep the current backend)."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, choice):
        super().__init__()
        self._choice = choice             # onboard.Choice (kind == "api")

    def compose(self) -> ComposeResult:
        c = self._choice
        with Vertical(id="keyprompt"):
            t = Text()
            t.append(f"{c.label} ", style=f"bold {c.brand_hex or _PAL['accent']}")
            t.append("needs an API key", style=_PAL["text"])
            yield Static(t, id="keyprompt-title")
            yield Static(
                Text(f"Paste your {c.key_env} to switch. Saved to "
                     "~/.cc-copilot.toml (chmod 600); a real env var still wins.",
                     style=_PAL["muted"]),
                id="keyprompt-intro")
            yield Input(placeholder=f"{c.key_env} — paste & Enter",
                        password=True, id="keyprompt-key")
            yield Static("", id="keyprompt-hint")
            with Horizontal(id="keyprompt-actions"):
                yield Button("Save & switch", id="keyprompt-save", variant="primary")
                yield Button("Cancel", id="keyprompt-cancel")

    def on_mount(self) -> None:
        self.query_one("#keyprompt-key", Input).focus()

    @on(Input.Submitted, "#keyprompt-key")
    def _on_submit(self, event) -> None:
        self._save()

    @on(Button.Pressed, "#keyprompt-save")
    def _on_save(self, event) -> None:
        self._save()

    @on(Button.Pressed, "#keyprompt-cancel")
    def _on_cancel(self, event) -> None:
        self.action_cancel()

    def _save(self) -> None:
        key = self.query_one("#keyprompt-key", Input).value.strip()
        if not key:                       # we only open when no key is set
            self.query_one("#keyprompt-hint", Static).update(
                Text(f"paste a {self._choice.key_env}, or Cancel to keep your "
                     "current backend.", style=_PAL["warning"]))
            self.query_one("#keyprompt-key", Input).focus()
            return
        self.dismiss(key)

    def action_cancel(self) -> None:
        self.dismiss(None)


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
    #chat-pin {
        width: 100%; height: 1; min-height: 1;
        background: $boost; color: $text-muted; padding: 0 1;
        text-wrap: nowrap;
    }
    #chat-pin:hover { background: $secondary 20%; }

    /* status + composer flow at the bottom (above the docked Footer); no
       competing dock:bottom so the composer box is always visible. */
    /* height:auto so a narrow sidebar can reflow the status into stacked rows
       (no field gets cropped); bounded so the long HUD can't starve #chat. The
       rows are width-packed (see _pack_rows) so they don't soft-wrap, so the cap
       only needs to clear the worst packed case (~9 rows at a 30-col sidebar with
       a full HUD). text-wrap:wrap is the safety net for a single over-long field. */
    #status {
        height: auto; min-height: 1; max-height: 12;
        background: $boost; color: $text; padding: 0 1; text-wrap: wrap;
    }
    /* rotating feature tip — one subtle muted line on the flat ground, just
       above the composer. height:1 + no wrap so a long tip clips instead of
       growing the row and stealing space from the chat. */
    #tip { height: 1; padding: 0 1; color: $text-muted; text-wrap: nowrap; }
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
    /* /command results render inline (no collapsible box) with an accent bar so
       they read as cockpit output, distinct from the primary-barred Q&A. The
       Markdown widget gets no top margin so its bar butts against the header. */
    .role-meta      { border-left: outer $accent;    padding-left: 1; }
    Markdown.role-meta { margin: 0; }
    /* timestamp header row above each chat turn — role label left, dim time right.
       No bottom margin so it sits flush on its message body (shared gutter bar). */
    .turn-head { height: 1; margin: 0; }
    Markdown.role-assistant { margin: 0; }

    Picker { align: center middle; }
    MultiPicker { align: center middle; }
    WelcomeScreen { align: center middle; }
    #welcome { width: 74; max-width: 94%; height: auto; max-height: 90%;
               background: $surface; border: round $accent; padding: 1 2; }
    #welcome-title { text-style: bold; color: $primary; }
    #welcome-intro { color: $text-muted; margin-bottom: 1; }
    #welcome-choices { height: auto; width: 100%; margin-bottom: 1;
                       background: $panel; border: round $accent; padding: 0 1; }
    #welcome-key { margin-bottom: 1; }
    #welcome-hint { color: $text-muted; height: auto; min-height: 1; margin-bottom: 1; }
    #welcome-actions { height: auto; align: right middle; }
    #welcome-actions Button { margin-left: 2; }
    KeyPrompt { align: center middle; }
    #keyprompt { width: 66; max-width: 92%; height: auto; max-height: 80%;
                 background: $surface; border: round $accent; padding: 1 2; }
    #keyprompt-title { text-style: bold; margin-bottom: 1; }
    #keyprompt-intro { color: $text-muted; margin-bottom: 1; }
    #keyprompt-key { margin-bottom: 1; }
    #keyprompt-hint { color: $text-muted; height: auto; min-height: 1; margin-bottom: 1; }
    #keyprompt-actions { height: auto; align: right middle; }
    #keyprompt-actions Button { margin-left: 2; }
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

    /* Notifications. Default Textual toasts dock bottom-RIGHT (over the composer,
       so they obscured the prompt box) and are a wide, deeply-padded block. Move
       them to the top-right corner and slim them down to match the cockpit: the
       same `outer ▌` accent bar as the chat role rows, the $boost surface of the
       status strip, auto width, and single-row padding — so a toast reads as a
       quiet cockpit element, not a popup. */
    ToastRack { dock: top; align: right top; margin: 1 1 0 0; }
    Toast {
        width: auto; max-width: 46; min-width: 14;
        padding: 0 1; margin-top: 1;
        background: $boost; tint: white 0%;
        border-left: outer $primary;
    }
    Toast.-information { border-left: outer $primary; }
    Toast.-warning    { border-left: outer $warning; }
    Toast.-error      { border-left: outer $error; }
    .toast--title { text-style: bold; }
    """

    # Footer shows only the few highest-value keys; the rest are still bound but
    # `show=False` (they'd crowd a narrow cockpit). The hidden ones — resize,
    # refresh, clear — surface instead in the rotating tip line above the
    # composer (see _TIPS / _rotate_tip), which is where discovery now lives.
    BINDINGS = [
        Binding("ctrl+c", "quit", "quit"),
        # Ctrl+Y copies the current text selection (Textual's native drag-select →
        # system clipboard via OSC 52, works over tmux/SSH). Copy gets its own key
        # so Ctrl+C is never ambiguous about quitting; ⌘C is intercepted by the
        # terminal and never reaches us.
        # priority=True is REQUIRED: the composer (a focused TextArea) otherwise
        # swallows ctrl+y, so a non-priority app binding never fires. Priority
        # bindings are checked before the focused widget. (ctrl+n still moves down
        # in an open Picker — handled in the picker's own key handler.)
        Binding("ctrl+y", "copy_selection", "copy", priority=True),
        # Ctrl+Z interrupts the in-flight answer (keeps the cockpit running) —
        # "queue by default, interrupt on demand". priority=True so it fires even
        # while the composer (a focused TextArea) has focus; this overrides the
        # TextArea's ctrl+z undo, a deliberate trade for a reliable stop key.
        Binding("ctrl+z", "stop_answer", "stop", priority=True),
        Binding("ctrl+t", "model", "model"),
        Binding("ctrl+r", "refresh_now", "refresh", show=False),
        Binding("ctrl+l", "clear_chat", "clear view", show=False),
        # resize the activity timeline (the chat fills the rest); persisted.
        # priority so it works while the composer is focused. Shift+arrows are
        # the primary keys — macOS grabs Ctrl+Up/Down for Mission Control, so
        # those stay as a hidden alias for platforms where they get through.
        # show=False: it was the noisiest pair in the footer (the user's ask) —
        # it lives in the tips now.
        Binding("shift+up", "grow_timeline", "taller", priority=True, show=False),
        Binding("shift+down", "shrink_timeline", "shorter", priority=True, show=False),
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
        self._out_exact = False          # backend-reported (no ~) vs chars/4 estimate
        self._last_cost = None           # USD for the last turn, when reported
        self._stream_md = None           # live-streaming Markdown widget (if any)
        self._stream_buf = ""            # chunks so far (re-mount + token estimate)
        self._answer_stop = threading.Event()
        self._answer_handle = None       # in-flight StreamHandle (cancel target)
        self._answer_store = None        # the conversation the answer belongs to
        self._answer_abandoned = False   # /forget mid-stream: drop, don't persist
        self._answer_stopped = False     # ctrl+z /stop: end this turn, keep app
        self._chat_answer_inflight = False  # a streaming chat answer is running (vs /now,/since)
        self._msg_queue = []             # chat messages typed while busy (FIFO)
        self._prompt_history = self._prompt_history_from_session()
        self._prompt_history_index = None
        self._prompt_draft = ""
        self._chat_prompt_nav_index = None
        self._chat_pin_index = None
        self._chat_pin_scroll_sig = None
        self._rewind_esc_at = 0.0
        self._slash_open = False
        self._watch_stop = threading.Event()
        self._watch_path = None
        self._watch_size = -1
        self._watch_state = None
        self._timeline_sig = None       # evidence identity of the last rebuild

    # ---- prompt history (composer ↑/↓, terminal-style) ----
    def _prompt_history_from_session(self) -> list:
        return [text for role, text in getattr(self.session, "history", [])
                if role == "user" and str(text or "").strip()]

    def _sync_prompt_history_from_session(self) -> None:
        self._prompt_history = self._prompt_history_from_session()
        self._reset_prompt_history_nav()

    def _remember_prompt(self, text: str) -> None:
        text = str(text or "").strip()
        if text and (not self._prompt_history or self._prompt_history[-1] != text):
            self._prompt_history.append(text)
        self._reset_prompt_history_nav()
        self._cancel_rewind_esc()

    def _reset_prompt_history_nav(self) -> None:
        self._prompt_history_index = None
        self._prompt_draft = ""

    def _prompt_history_prev(self, draft: str):
        if not self._prompt_history:
            return None
        if self._prompt_history_index is None:
            self._prompt_draft = draft or ""
            self._prompt_history_index = len(self._prompt_history) - 1
        else:
            self._prompt_history_index = max(0, self._prompt_history_index - 1)
        return self._prompt_history[self._prompt_history_index]

    def _prompt_history_next(self, draft: str):
        if self._prompt_history_index is None:
            return None
        if self._prompt_history_index >= len(self._prompt_history) - 1:
            self._prompt_history_index = None
            return self._prompt_draft
        self._prompt_history_index += 1
        return self._prompt_history[self._prompt_history_index]

    def _cancel_rewind_esc(self) -> None:
        self._rewind_esc_at = 0.0

    def _empty_composer_escape(self) -> None:
        if not any(role == "user" for role, _ in getattr(self.session, "history", [])):
            return
        now = time.monotonic()
        if now - self._rewind_esc_at <= 1.2:
            self._rewind_esc_at = 0.0
            self.action_rewind()
            return
        self._rewind_esc_at = now
        self.notify("Esc again to rewind", severity="information", timeout=2)

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
        chat_pin = Static("", id="chat-pin")
        chat = VerticalScroll(id="chat")
        # The timeline and chat are display-only. Keep them out of the focus
        # chain so a click (or Tab) can never strand focus on a scroll pane —
        # that used to leave typed / IME (e.g. Chinese) input with no target.
        # Mouse-wheel scrolling still works without focus.
        header.can_focus = timeline.can_focus = chat_pin.can_focus = chat.can_focus = False
        yield header
        yield timeline
        yield chat_pin
        yield chat
        yield Static("", id="status")
        yield Static("", id="tip")              # rotating feature tip (subtle)
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
        composer.border_subtitle = "Enter send · Ctrl+J newline · / commands"
        composer.focus()
        self._rotate_tip()                                  # show one immediately
        self.set_interval(16, self._rotate_tip, name="tips")
        self.set_interval(0.35, self._sync_chat_pin_to_scroll, name="chat-pin")
        self.set_interval(0.12, self._tick_busy, name="busy-spinner")
        self.set_interval(self.poll, self._tick_refresh, name="auto-refresh")
        if self.alerts:
            self.watch_agent()
        # first launch (no config yet, no explicit --backend): greet with the
        # model picker over the dimmed cockpit. Deferred so the cockpit paints
        # behind it first.
        if self.backend is None and OB.needs_onboarding():
            self.call_after_refresh(self.action_onboard)

    def action_onboard(self) -> None:
        """Open the first-run model picker (also reachable later via /init)."""
        self.push_screen(WelcomeScreen(OB.detect(featured_only=True)),
                         self._after_onboard)

    def _after_onboard(self, result) -> None:
        if not result:
            return
        name, choice = result
        if not choice or choice.kind == "skip":
            self.notify("no model set — pick one anytime with /init", severity="information")
            self._update_status()
            return
        # /init already wrote the config via the onboard screen, so don't follow
        # up with the "make this your default?" prompt.
        self._set_backend(name, offer_default=False)   # applies to the live session + UI
        if choice.kind == "api":
            self.model = self.session.model = choice.default_model or self.model
        else:
            # a CLI backend uses its own default — drop any stale API model
            # (e.g. a prior gpt-4o) so we don't pass it to `claude --model …`.
            self.model = self.session.model = None
        self._update_header()
        self._update_status()
        self.notify(f"model ready · {choice.label}", severity="information")

    def on_unmount(self):
        self._busy = False
        self._watch_stop.set()
        # The worker is usually BLOCKED inside the backend read, where the stop
        # flag can't reach it — cancel() kills the transport (subprocess/socket)
        # so the read returns NOW. Without it, quit hangs until the backend's
        # next output or the stream timeout (Textual joins thread workers on
        # shutdown via the default executor).
        self._answer_stop.set()
        h = self._answer_handle
        if h is not None:
            h.cancel()
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
            room = self.size.height - 11   # header+status+tip+composer+footer+min chat
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

    # ---- rotating feature tips (the slimmed footer's discoverability moved here) ----
    def _next_tip(self) -> str:
        """Walk a shuffled order so tips feel random but don't repeat until the
        whole set has been shown; reshuffle each pass."""
        if not _TIPS:
            return ""
        order = getattr(self, "_tip_order", None)
        if not order:
            order = list(range(len(_TIPS)))
            random.shuffle(order)
            self._tip_order, self._tip_i = order, 0
        idx = self._tip_order[self._tip_i]
        self._tip_i += 1
        if self._tip_i >= len(self._tip_order):
            random.shuffle(self._tip_order)
            self._tip_i = 0
        return _TIPS[idx]

    def _rotate_tip(self) -> None:
        self._current_tip = self._next_tip()
        self._render_tip()

    def _render_tip(self) -> None:
        """Paint the #tip line: a contextual 'Ctrl+Y to copy' prompt whenever text
        is selected, otherwise the current rotating feature tip."""
        try:
            w = self.query_one("#tip", Static)
        except NoMatches:
            return
        try:
            selected = bool(self.screen.get_selected_text())
        except Exception:
            selected = False
        t = Text()
        if selected:
            t.append("📋 ", style=_PAL["accent"])
            t.append("Ctrl+Y to copy the selection", style=_PAL["secondary"])
        elif getattr(self, "_current_tip", ""):
            t.append("💡 ", style=_PAL["accent"])
            t.append(self._current_tip, style=_PAL["muted"])
        else:
            return
        w.update(t)

    def on_text_selected(self, event) -> None:
        """Textual fires this when the user drag-selects message text — surface the
        copy hint in the tip line right away (not just on the 16s tip rotation)."""
        self._render_tip()

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
        widget = getattr(event, "widget", None) or getattr(event, "control", None)
        if getattr(widget, "id", "") == "chat-pin":
            if self._chat_pin_index is not None:
                self._jump_chat_prompt(target=self._chat_pin_index)
            try:
                event.stop()
            except Exception:
                pass
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
        yield SystemCommand("Now", "Recommend the next step (LLM; deterministic fallback)",
                            self.action_now)
        yield SystemCommand("Brief", "Evidence-cited recap", self.action_brief)
        yield SystemCommand("Check", "Safety / off-track assessment", self.action_check)
        yield SystemCommand("Diff", "What changed since last turn", self.action_diff)
        yield SystemCommand("Status", "Fleet board — every session in this project, neediest first",
                            self.action_status)
        yield SystemCommand("Evidence", "Choose one or more agent sessions",
                            self.action_sessions)
        yield SystemCommand("Target", "Show the current cockpit target", self.action_target)
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

    def _prompt_first_line(self, text: str, limit: int = 90) -> str:
        first = (str(text or "").splitlines() or [""])[0].strip()
        return _short_activity(first or "(empty prompt)", limit)

    def _prompt_widget(self, text: str, hhmm=None):
        hhmm = self._hhmm_now() if hhmm is None else hhmm
        body = Group(self._head_grid("you", _PAL["secondary"], hhmm),
                     Text("› " + str(text or ""), style="bold"))
        w = self._role(body, "role-user")
        w._cc_prompt_text = str(text or "")
        return w

    def _chat_prompt_widgets(self) -> list:
        try:
            chat = self.query_one("#chat", VerticalScroll)
            return list(chat.query(".role-user"))
        except Exception:
            return []

    def _update_chat_pin(self, index=None) -> None:
        try:
            pin = self.query_one("#chat-pin", Static)
        except Exception:
            return
        prompts = self._chat_prompt_widgets()
        if not prompts:
            self._chat_pin_index = None
            pin.update(Text("prompt · none yet", style=_PAL["muted"]))
            return
        if index is None:
            index = self._chat_prompt_nav_index
        if index is None or not (0 <= index < len(prompts)):
            index = len(prompts) - 1
        index = max(0, min(len(prompts) - 1, int(index)))
        self._chat_pin_index = index
        text = getattr(prompts[index], "_cc_prompt_text", "")
        t = Text("prompt ", style=_PAL["muted"])
        t.append(f"{index + 1}/{len(prompts)}", style=_PAL["secondary"])
        t.append(" · ", style=_PAL["muted"])
        t.append(self._prompt_first_line(text), style=_PAL["text"])
        pin.update(t)

    def _prompt_index_at_chat_top(self):
        prompts = self._chat_prompt_widgets()
        if not prompts:
            return None
        try:
            chat = self.query_one("#chat", VerticalScroll)
            top = int(chat.scroll_offset.y)
        except Exception:
            return len(prompts) - 1
        if getattr(chat, "max_scroll_y", 0) <= 0:
            cur = self._chat_prompt_nav_index
            return cur if cur is not None and 0 <= cur < len(prompts) else len(prompts) - 1
        best = 0
        for i, widget in enumerate(prompts):
            try:
                y = int(widget.virtual_region.y)
            except Exception:
                continue
            if y <= top:
                best = i
            else:
                break
        return best

    def _sync_chat_pin_to_scroll(self) -> None:
        prompts = self._chat_prompt_widgets()
        if not prompts:
            self._chat_pin_scroll_sig = None
            self._update_chat_pin()
            return
        try:
            chat = self.query_one("#chat", VerticalScroll)
            sig = (int(chat.scroll_offset.y), len(prompts))
        except Exception:
            sig = (None, len(prompts))
        if sig == self._chat_pin_scroll_sig:
            return
        self._chat_pin_scroll_sig = sig
        index = self._prompt_index_at_chat_top()
        if index is not None:
            self._chat_prompt_nav_index = index
            self._update_chat_pin(index)

    def _jump_chat_prompt(self, delta=0, target=None) -> bool:
        prompts = self._chat_prompt_widgets()
        if not prompts:
            self._update_chat_pin()
            return False
        if target is None:
            cur = self._chat_prompt_nav_index
            if cur is None or not (0 <= cur < len(prompts)):
                cur = len(prompts) - 1
            index = cur + int(delta or 0)
        else:
            index = int(target)
        index = max(0, min(len(prompts) - 1, index))
        self._chat_prompt_nav_index = index
        widget = prompts[index]
        chat = self.query_one("#chat", VerticalScroll)
        scroller = getattr(chat, "scroll_to_widget", None)
        if callable(scroller):
            try:
                scroller(widget, animate=False, top=True, force=True, immediate=True)
            except TypeError:
                scroller(widget, animate=False)
            except Exception:
                try:
                    chat.scroll_to(y=max(0, int(widget.virtual_region.y)),
                                   animate=False, force=True)
                except Exception:
                    chat.scroll_end(animate=False)
        else:
            try:
                chat.scroll_to(y=max(0, int(widget.virtual_region.y)),
                               animate=False, force=True)
            except Exception:
                chat.scroll_end(animate=False)
        try:
            self._chat_pin_scroll_sig = (int(chat.scroll_offset.y), len(prompts))
        except Exception:
            self._chat_pin_scroll_sig = None
        self._update_chat_pin(index)
        self._focus_composer()
        return True

    def _chat(self, widget):
        chat = self.query_one("#chat", VerticalScroll)
        chat.mount(widget)
        if hasattr(widget, "has_class") and widget.has_class("role-user"):
            self._chat_prompt_nav_index = len(self._chat_prompt_widgets()) - 1
        self._update_chat_pin()
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
        self._msg_queue.clear()         # queued messages belong to the old context
        self._chat_prompt_nav_index = None
        hist = self.session.history
        if hist:
            n = len(hist) // 2
            chat.mount(self._role(
                Text(f"── restored {n} prior turn{'s' if n != 1 else ''} ──", "dim"),
                "role-event"))
            # per-turn stamps from the store, aligned 1:1 with history; fall back
            # to no time if the store is off or the two ever drift out of length.
            times = self.session.store.load_turn_times()
            if len(times) != len(hist):
                times = [""] * len(hist)
            for i, (role, txt) in enumerate(hist):
                hhmm = times[i]
                if role == "user":
                    chat.mount(self._prompt_widget(txt, hhmm))
                else:
                    chat.mount(self._assistant_head(hhmm))
                    chat.mount(Markdown(txt, classes="role-assistant"))
        prompts = self._chat_prompt_widgets()
        if prompts:
            self._chat_prompt_nav_index = len(prompts) - 1
        self._update_chat_pin()
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
        return self._status_body(w)

    def _status_body(self, w: int) -> Text:
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
            hud_parts = ans.split(" · ") if ans else []
            if self._msg_queue:
                hud_parts = hud_parts + [f"+{len(self._msg_queue)} queued"]
            hud_str = _busy_indicator(self._busy_frame) + (
                (" · " + " · ".join(hud_parts)) if hud_parts else "")
            hud_sty = _PAL["accent"]
        elif self._ctx_stats is not None:
            hud_str = EC.format_hud(self._ctx_stats, self._out_tokens,
                                    out_exact=self._out_exact,
                                    cost_usd=self._last_cost)
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
        # watched session + idle: together if they fit, else one per row.
        watch_txt = "↳ " + watch_lbl
        t.append("\n")
        t.append(watch_txt, style=watch_sty)
        if _cell_len(watch_txt + " · " + idle) <= w:
            t.append(" · " + idle, style=_PAL["muted"])
        else:
            t.append("\n")
            t.append(idle, style=_PAL["muted"])
        # HUD: greedy-pack the " · " parts so every row fits w (no soft-wrap →
        # the rendered height equals the row count and stays under the cap, so
        # nothing is clipped). trimmed keeps its own warning-colored row.
        if self._busy:
            for row in _pack_rows([_busy_indicator(self._busy_frame)] + hud_parts, w):
                t.append("\n")
                t.append(row, style=hud_sty)
        elif hud_parts:
            core = [p for p in hud_parts if p != "trimmed"]
            for row in _pack_rows(core, w):
                t.append("\n")
                t.append(row, style=hud_sty)
            if "trimmed" in hud_parts:
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
        self._remember_prompt(text)
        if text.startswith("/"):
            self._meta(text)
            return
        if self._busy:
            # don't drop it — queue it behind the live answer and send it when the
            # current turn finishes (drained in _answer_done / _now_done /
            # _since_done). FIFO, sequential. The bubble is NOT rendered now: it's
            # mounted when the turn actually runs (_begin_chat_turn), so a queued
            # prompt can never appear above the answer it is waiting on.
            self._prune_stale_queue()           # so the cap counts only live-context
            if len(self._msg_queue) >= _MSG_QUEUE_MAX:
                self.notify(f"queue full ({_MSG_QUEUE_MAX}) — wait for the current "
                            "answer", severity="warning")
                return
            # bind the message to the evidence context it was typed in, so a switch
            # (/use, /here, /scope, /sessions) before it drains never answers it
            # against the wrong session/scope — it's dropped instead (see _drain).
            self._msg_queue.append((text, self._evidence_sig(), self.session.store))
            self.notify(f"queued #{len(self._msg_queue)} — sends after the current "
                        "answer", severity="information")
            self._update_status()
            return
        self._begin_chat_turn(text)

    def _queue_item_live(self, item) -> bool:
        """True if a queued (text, sig, store) still belongs to the current
        evidence context — same scope/session and the same conversation."""
        _text, sig, store = item
        return sig == self._evidence_sig() and self._same_conv(store, self.session.store)

    def _prune_stale_queue(self) -> int:
        """Drop queued messages whose evidence context no longer matches. Returns
        how many were dropped (silent — the caller decides whether to announce)."""
        before = len(self._msg_queue)
        self._msg_queue[:] = [m for m in self._msg_queue if self._queue_item_live(m)]
        return before - len(self._msg_queue)

    def _begin_chat_turn(self, text: str) -> bool:
        """Render the prompt bubble and launch one chat turn. Returns False if a
        guard blocked it (no live session / no backend) so the queue drainer can
        stop cleanly. The bubble mounts here — right before its own answer — so
        immediate and queued turns both render in correct Q/A order."""
        if self.session.st is None:
            self.notify("history-only view (transcript gone) — /sessions to attach a "
                        "live session", severity="warning")
            return False
        if not N.available(self.backend):
            self.notify("no backend — /model to switch", severity="error")
            return False
        self._chat(self._prompt_widget(text))
        self.session.refresh()
        ctx = self.session.answer_context(text, history=list(self.session.history))
        self._ctx_stats = ctx.stats
        self._out_tokens = 0
        self._out_exact = False
        self._last_cost = None
        self._stream_md = None
        self._stream_buf = ""
        self._answer_abandoned = False
        self._answer_store = self.session.store
        self.session.last_context_stats = ctx.stats
        self.session.last_output_tokens = 0
        origin = self._answer_origin(self.session.st, self.session.store)
        self._busy = True
        self._busy_frame = 0
        self._chat_answer_inflight = True   # interruptible by /stop even pre-handle
        self._update_status()
        # Capture the originating conversation (store + state). If the user
        # switches sessions before the backend returns, the answer is recorded
        # against the session it was ASKED in — not whatever is current now.
        self._answer(text, ctx.text, self.session.st, list(self.session.history),
                     self.session.store, origin)
        return True

    def _drain_msg_queue(self) -> None:
        """Start the next queued chat message, if any (called when a turn ends).

        A message only runs if the evidence context (scope/session/store) still
        matches the one it was queued in; messages stranded by a switch are
        dropped, never answered against the wrong session."""
        if self._busy or not self._msg_queue:
            return
        stale = self._prune_stale_queue()
        if stale:
            self.notify(f"dropped {stale} queued message(s) from a previous context",
                        severity="warning")
        if not self._msg_queue:
            return
        text, _sig, _store = self._msg_queue.pop(0)
        if not self._begin_chat_turn(text) and self._msg_queue:
            # a guard blocked it (backend vanished) — drop the rest, don't spin.
            self.notify(f"dropped {len(self._msg_queue)} queued message(s)",
                        severity="warning")
            self._msg_queue.clear()

    def action_stop_answer(self) -> None:
        """Ctrl+Z / `/stop`: interrupt the in-flight answer without quitting the
        app. A decisive stop — the pending queue is cleared so you can steer."""
        if not self._busy:
            self.notify("nothing to stop", severity="information")
            return
        cleared = len(self._msg_queue)
        self._msg_queue.clear()
        if not self._chat_answer_inflight:
            # a non-streaming /now or /since is running — no transport to cancel;
            # it finishes on its own. We can still clear the queue.
            msg = "can't interrupt /now·/since mid-run — it'll finish shortly"
            self.notify(msg + (f" · cleared {cleared} queued" if cleared else ""),
                        severity="warning")
            self._update_status()
            return
        # A chat answer is running (or just starting). Set the stop flag FIRST so
        # the worker honors it even if it hasn't published the handle yet (the
        # handle-race), then cancel the transport if it's already live.
        self._answer_stopped = True     # _answer_done renders a neutral stop, no save
        h = self._answer_handle
        if h is not None:
            h.cancel()                  # unblock the streaming read NOW; worker unwinds
        self.notify("⏹ stopping the current answer"
                    + (f" · cleared {cleared} queued" if cleared else ""),
                    severity="warning")
        self._update_status()

    @work(thread=True)
    def _answer(self, text, brief_text, st, history, store, origin=None):
        import time as _time
        h = None
        origin = origin or {}
        backend = origin.get("backend", self.backend)
        model = origin.get("model", self.model)
        try:
            h = N.chat_brief_stream(brief_text, [], text, model=model,
                                    backend=backend)
            self._answer_handle = h     # on_unmount/_abandon cancel() this
            if self._answer_stopped:    # ctrl+z landed before the handle existed
                h.cancel()              # honor it now (the handle-race window)
            # coalesce: claude emits token-level deltas (can be > 20/s); batch
            # anything that arrives within 50ms into one UI update so the loop
            # paints words, not keystrokes. Chunks slower than that flush as-is.
            pending = ""
            last_flush = 0.0
            for chunk in h:
                if self._answer_stop.is_set():
                    return              # app is closing; on_unmount cancelled
                                        # the transport, teardown reaps it
                pending += chunk
                now = _time.monotonic()
                if now - last_flush >= 0.05:
                    self.call_from_thread(self._answer_chunk, store, pending)
                    pending = ""
                    last_flush = now
            if pending and not self._answer_stop.is_set():
                self.call_from_thread(self._answer_chunk, store, pending)
            ans, ok = (h.text or ""), True
            if not ans:
                ans, ok = "# error: backend returned no output", False
        except Exception as e:
            ans, ok = f"# error: {e}", False
        finally:
            self._answer_handle = None
        try:
            self.call_from_thread(self._answer_done, text, ans, ok, st, store,
                                  h.usage if h is not None else None, origin)
        except Exception:
            pass                        # app already shut down mid-answer

    @staticmethod
    def _same_conv(store, current):
        """Is this the conversation the user is looking at? Object identity OR
        the same durable conversation — switching away and back (/resume,
        /history) builds a NEW Store object for the SAME conv_id, and the
        completed answer should still render there."""
        if store is current:
            return True
        cid = getattr(store, "conv_id", None)
        return cid is not None and cid == getattr(current, "conv_id", None)

    def _answer_chunk(self, store, chunk):
        """Paint one streamed chunk (UI thread). Faithfulness guards: chunks for
        a conversation the user has switched away from are not PAINTED (the
        full turn still lands in ITS store via _answer_done) — but they always
        accumulate in ``_stream_buf``, which belongs to the in-flight ANSWER,
        not to the view: switching away and back mid-stream re-mounts from the
        buffer, and a hidden-while-away gap must not leave a hole in the
        visible text. A widget removed mid-stream (/clear) is likewise
        re-mounted with the accumulated text."""
        if self._answer_abandoned:
            return
        self._stream_buf += chunk
        self._out_tokens = EC.estimate_tokens(self._stream_buf)
        self.session.last_output_tokens = self._out_tokens
        if not self._same_conv(store, self.session.store):
            return                      # buffered, just not painted here
        md = self._stream_md
        if md is None or not md.is_attached:
            md = Markdown(self._stream_buf, classes="role-assistant")
            self._stream_md = md
            chat = self.query_one("#chat", VerticalScroll)
            chat.mount(md)
            if hasattr(chat, "anchor"):
                chat.anchor()           # follow growth; released by user scroll
            else:
                chat.scroll_end(animate=False)
            return
        if hasattr(md, "append"):
            # incremental: re-parses only the trailing block, and the returned
            # AwaitComplete schedules itself (fire-and-forget is the contract)
            md.append(chunk)
        else:                           # very old Textual: full re-render
            md.update(self._stream_buf)

    def _answer_origin(self, st, store) -> dict:
        return {
            "hhmm": self._hhmm_now(),       # when the turn began → the answer's stamp
            "backend": self.backend,
            "model": self.model,
            "scope": self.session.scope,
            "scope_sessions": list(self.session.scope_sessions or []),
            "transcript": (os.path.abspath(self.session.path) if self.session.path
                           else getattr(store, "transcript", "")),
        }

    def _restore_current_store_meta(self) -> None:
        """Keep the conversation header pointed at the currently selected
        evidence after an older in-flight answer writes its own turn source."""
        store = self.session.store
        store.scope = self.session.scope
        store.scope_sessions = list(self.session.scope_sessions or [])
        if self.session.path:
            store.transcript = os.path.abspath(self.session.path)
        store.record_state(self.session.st, scope=self.session.scope,
                           scope_sessions=self.session.scope_sessions,
                           backend=self.backend, model=self.model)

    def _answer_done(self, text, ans, ok, st, store, usage=None, origin=None):
        self._busy = False
        self._busy_frame = 0
        self._chat_answer_inflight = False
        self._answer_store = None
        # consume the stop flag HERE — before any early return — so a ctrl+z that
        # raced an abandon (/forget, /rewind) can't leak into the next turn.
        stopped = self._answer_stopped
        self._answer_stopped = False
        md, buf = self._stream_md, self._stream_buf
        self._stream_md = None
        self._stream_buf = ""
        origin = origin or {}
        if self._answer_abandoned:
            # /forget mid-stream: the user deleted this conversation while the
            # answer was in flight — render nothing, persist nothing (a write
            # here would resurrect the files /forget just removed).
            self._answer_abandoned = False
            self._update_header()
            self._update_status()
            self._drain_msg_queue()     # a prompt queued during the abandon window
            return
        if stopped:
            ok = False                  # a user stop is never a persisted success
        same = self._same_conv(store, self.session.store)
        exact = bool(ok and usage and getattr(usage, "exact", False)
                     and getattr(usage, "output_tokens", 0))
        if same:
            # HUD state belongs to the conversation the turn ran in — never
            # leak turn A's exact tokens/cost onto conversation B's status bar.
            self._out_exact = exact
            self._last_cost = getattr(usage, "cost_usd", None) if (ok and usage) else None
            self._out_tokens = (usage.output_tokens if exact
                                else (EC.estimate_tokens(ans) if ok else self._out_tokens))
            self.session.last_output_tokens = self._out_tokens
            self.session.last_usage = usage if ok else None
        # `_pruning` is set synchronously by a same-tick remove_children() (a
        # /clear, or a mid-stream history rebuild) BEFORE the async detach lands,
        # so is_attached still reads True for a widget about to vanish. Treat a
        # pruning md as already gone — Textual itself gates on `not is_attached or
        # _pruning`. Otherwise we'd mount the header before a doomed md and strand
        # it as an orphaned "copilot HH:MM" with no answer once the prune runs.
        gone = md is None or not md.is_attached or getattr(md, "_pruning", False)
        live = not gone
        if ok:
            # the cockpit's single durable write-site (the REPL has its own in
            # ChatSession._finalize_turn); _answer runs on a worker thread, hence here.
            # Persist to the originating store, even if the user has switched away.
            store.scope = origin.get("scope", self.session.scope)
            store.scope_sessions = list(origin.get("scope_sessions",
                                                   self.session.scope_sessions) or [])
            if origin.get("transcript"):
                store.transcript = origin["transcript"]
            store.record_turn(text, ans, st=st,
                              backend=origin.get("backend", self.backend),
                              model=origin.get("model", self.model),
                              usage=(usage.as_dict() if usage else None))
            if same:
                self.session.history.append(("user", text))
                self.session.history.append(("assistant", ans))
                hhmm = origin.get("hhmm") or self._hhmm_now()
                if live:                # streamed & still on screen: header on md
                    self.query_one("#chat", VerticalScroll).mount(
                        self._assistant_head(hhmm), before=md)
                elif md is None or not md.is_attached:
                    # fallback backend (nothing streamed), or md already fully
                    # detached — render the turn fresh: header + answer.
                    self._chat(self._assistant_head(hhmm))
                    self._chat(Markdown(ans, classes="role-assistant"))
                # else: md is mid-prune (the pane was just cleared/rebuilt) —
                # render nothing so no lone header is stranded; the turn is
                # persisted and reappears on the next rebuild.
                self._restore_current_store_meta()
            # if switched away: the turn is safe on disk and reappears on return,
            # so we don't render it into the now-current (different) conversation.
        elif same and stopped:
            # user-initiated stop (ctrl+z): neutral note, keep the partial on
            # screen, persist nothing. Not an error.
            note = "⏹ stopped" + (" — partial answer above kept (not saved)"
                                   if (live and buf) else "")
            self._chat(self._role(Text(note, style=_PAL["muted"]), "role-event"))
        elif same:
            # keep any partial text the user already saw; the error goes beneath
            # it. The partial is NOT recorded — only completed answers persist.
            note = " — partial answer above was not saved" if (live and buf) else ""
            self._chat(self._role(Text(ans + note, style=_PAL["error"]), "role-alert"))
        self._update_header()
        self._update_status()
        self._drain_msg_queue()         # send the next queued message, if any

    # ---- /since: deterministic delta, narrated into a grounded recap ----
    def _since_cmd(self, arg: str):
        """Recap by default (grounded in the cited delta), with the deterministic
        evidence beneath it; instant deterministic view for `--raw`, no backend,
        nothing-new, or while busy. The model call runs off the UI thread."""
        title = (f"/since {arg}").strip()
        window_arg, instruction = self.session._split_since_arg(arg)
        res = self.session._since_view(window_arg)
        if isinstance(res, str):                 # edge-case message (no mark, etc.)
            self._result(res, markdown=False, title=title)
            return
        view, raw, commit = res
        if raw or view.nothing_new or not N.available(self.backend) or self._busy:
            self._result(view.text)              # deterministic markdown, instant
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
        self._since_recap(title, view, (self._evidence_sig(), self.session.store),
                          commit, instruction)

    @work(thread=True)
    def _since_recap(self, title, view, origin, commit, instruction=""):
        try:
            recap = N.recap_since(view.text, model=self.model, backend=self.backend,
                                  instruction=instruction)
            out = self.session._compose_since(recap, view)
        except Exception as e:
            out = view.text + f"\n\n> _recap unavailable ({e}); evidence shown above._"
        self.call_from_thread(self._since_done, title, out, origin, commit)

    def _since_done(self, title, out, origin, commit):
        self._busy = False
        self._busy_frame = 0
        sig, store = origin
        if self._evidence_sig() == sig and self.session.store is store:
            self._result(out)                    # rendered markdown recap
            commit()                             # rendered → advance the marker
        else:
            self.notify(f"dropped {title} recap — you switched while it ran",
                        severity="warning")      # not committed → delta survives
        self._update_status()
        self._drain_msg_queue()         # a message queued during /since sends now

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
            self._result(_HELP_TEXT, markdown=False, title="/help"); return
        if low == "/observe":
            self.action_observe(); return
        if low == "/now" or low.startswith("/now "):
            self.action_now(cmd.strip()[4:].strip()); return
        if low == "/brief":
            self.action_brief(); return
        if low == "/check":
            self.action_check(); return
        if low == "/diff":
            self.action_diff(); return
        if low == "/status":
            self.action_status(); return
        if low == "/sessions":
            self.action_sessions(); return
        if low == "/target":
            self.action_target(); return
        if low == "/here":
            if not self.session.switch_to_here():
                self.notify("no current Claude/Codex session detected",
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
            self._sync_prompt_history_from_session()
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
            reg = BK.registry()
            if not arg:
                self.action_model()
            elif ":" in arg and arg.split(":", 1)[0].strip() in reg:
                # `/model deepseek:deepseek-v4-pro` — backend and model in one
                # go. Only when the prefix IS a backend: model ids themselves
                # can contain colons (OpenRouter's `…:free` / `…:nitro`).
                bname, _, mname = arg.partition(":")
                self._set_backend(bname.strip(), after_model=mname.strip() or None)
            elif arg in reg:
                self._set_backend(arg)
            else:
                ref = MODELS.resolve_ref(arg, self.backend or "")
                if ref:
                    bname, mname = ref
                    if bname == (self.backend or ""):
                        self._set_model(mname)
                    else:
                        self._set_backend(bname, after_model=mname)
                    return
                # not a backend name → treat as a model id on the CURRENT
                # backend (`/model deepseek-v4-pro`); free-form ids welcome
                self._set_model(arg)
            return
        if low == "/init" or low == "/onboard":
            self.action_onboard(); return
        if low == "/refresh":
            self.action_refresh_now(); return
        if low in ("/clear", "/cls"):
            self.action_clear_chat(); return
        if low in ("/stop", "/cancel"):
            self.action_stop_answer(); return
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
                self._result(out)                # markdown handoff
            return
        self.notify(f"unknown command {cmd!r}", severity="warning")

    @staticmethod
    def _hhmm_now() -> str:
        return time.strftime("%H:%M", time.localtime())

    @staticmethod
    def _head_grid(label, label_style, hhmm):
        """Header row for a chat turn — role label on the left, dim time on the
        right. An expanding grid hugs the time to the right edge at any width."""
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right")
        grid.add_row(Text(label, style=label_style), Text(hhmm or "", style=_PAL["muted"]))
        return grid

    def _assistant_head(self, hhmm):
        """The copilot turn's header as a standalone widget — mounted just above
        the answer's Markdown so they share the primary gutter bar as one block."""
        return Static(self._head_grid("copilot", _PAL["primary"], hhmm),
                      classes="role-assistant turn-head")

    def _result(self, body, *, markdown=True, title=None, cls="role-meta"):
        """Render a /command result inline in the chat — no collapsible box (the
        user's render choice). Markdown bodies (headings/bold/rules) render through
        the Markdown widget like a reply; pre-formatted Rich Text / columnar bodies
        (fleet board, diff, help) render verbatim with an optional dim title."""
        if markdown:
            self._chat(Markdown(str(body), classes=cls))
            return
        renderable = body if isinstance(body, Text) else Text(str(body))
        if title:
            head = Text(title + "\n", style=_PAL["muted"])
            head.append_text(renderable)
            renderable = head
        self._chat(self._role(renderable, cls))

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
        self._result(self.session.evidence().text)   # markdown brief
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
        self._result(body)                            # markdown observe board
        self._refresh_scope_view()

    # ---- /now: deterministic next-step, recommended via a grounded LLM call ----
    def action_now(self, instruction=""):
        """Recommend the next step. Deterministic-instant when there's no backend
        or a turn is already running; otherwise the model call runs off the UI
        thread and is dropped if the user switches evidence while it runs.
        ``instruction`` is an optional free-text steer (`/now in spanish`)."""
        self.session.refresh()
        if self._no_live():
            return
        try:
            det = O.next_step(self.session.path, self.session.st, self.session.scope,
                              sessions=self.session.scope_sessions)
        except ValueError as e:
            self.notify(str(e), severity="warning")
            return
        title = f"/now — {self.session.scope_label()}"
        if instruction:
            title += f' · "{instruction}"'
        if not N.available(self.backend) or self._busy:
            # deterministic next-step: plain text with indented `also:` siblings —
            # NOT markdown, so render verbatim (markdown would flatten the indent).
            body = det
            if instruction:                      # can't honor a steer without the model
                body += ("\n(the instruction needs the model — showing the "
                         "deterministic next-step)")
            self._result(body, markdown=False, title=title)
            self._update_status()
            return
        # Snapshot the evidence on the UI thread BEFORE the worker starts. Reading
        # session.evidence() inside the worker would let a switch-away/switch-back
        # race (which passes the origin check) recommend from the WRONG session's
        # evidence under the original title — so capture it here, like /since does
        # with its deterministic view.
        ev_text = self.session.evidence().text
        self._busy = True
        self._busy_frame = 0
        self._chat(self._role(
            Text("🧭 thinking about the next step — grounded in the evidence…",
                 style=_PAL["muted"]), "role-event"))
        self._update_status()
        self._now_recap(title, det, ev_text, instruction,
                        (self._evidence_sig(), self.session.store))

    @work(thread=True)
    def _now_recap(self, title, det, ev_text, instruction, origin):
        try:
            rec = N.next_step_brief(ev_text, model=self.model, backend=self.backend,
                                    instruction=instruction)
            out = self.session._compose_now(rec, det)
        except Exception as e:
            out = det + f"\n\n> _next-step recap unavailable ({e}); deterministic suggestion above._"
        self.call_from_thread(self._now_done, title, out, origin)

    def _now_done(self, title, out, origin):
        self._busy = False
        self._busy_frame = 0
        sig, store = origin
        if self._evidence_sig() == sig and self.session.store is store:
            self._result(out)                    # markdown next-step recap
        else:
            self.notify(f"dropped {title} — you switched while it ran",
                        severity="warning")
        self._update_status()
        self._drain_msg_queue()         # a message queued during /now sends now

    def action_check(self):
        self.session.refresh()
        if self._no_live():
            return
        body = (_check_text(self.session.st) if self.session.scope == SC.SESSION
                else self.session.evidence().text)
        self._result(body)                            # markdown check verdict
        self._update_status()

    def action_diff(self):
        self.session.refresh()
        if self._no_live():
            return
        self._result(self._diff_renderable(S.diff(self.session.prev, self.session.st)),
                     markdown=False, title="/diff — changes since last turn")

    def action_status(self):
        """Fleet board across the whole project — independent of the pinned
        evidence, so it works even in history-only mode."""
        from .chat import render_fleet
        cwd = self.session.cwd or os.getcwd()
        self._result(render_fleet(cwd)[0], markdown=False, title="/status — fleet board")

    def action_target(self):
        s = self.session
        body = (f"cockpit: {s.store.conv_id}\ntarget: {s.path}\n"
                f"evidence: {s.scope_label()}\n{s.banner()}")
        self._result(body, markdown=False, title="/target — current cockpit target")

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
            self._sync_prompt_history_from_session()
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
        if self._busy and self._same_conv(self._answer_store, self.session.store):
            # an in-flight answer FOR THIS conversation must not re-paint into the
            # fork or re-persist its (now-abandoned) turn when it completes —
            # abandon it exactly like action_forget (_answer_done drops it).
            self._answer_abandoned = True
            h = self._answer_handle
            if h is not None:
                h.cancel()
        question = qs[k]
        self.session.history = hist[:2 * k]             # turns are user/assistant pairs
        self.session.store.truncate(k)                  # persist the fork
        self._sync_prompt_history_from_session()
        self._rebuild_chat()
        comp = self.query_one("#composer", Composer)
        comp.text = question
        comp.move_cursor(comp.document.end)
        comp.focus()
        self.notify(f"rewound to message #{k + 1} — edit & Enter to re-ask")

    @work
    async def action_model(self):
        opts = []
        for name, be in sorted(BK.registry().items()):
            cur = "  ✓" if name == (self.backend or "") else ""
            avail = "" if be.available() else "  · " + be.reason()
            # show the model that selecting this backend would land on
            hint = ""
            if isinstance(be, BK.OpenAICompatBackend) and be.default_model:
                shown = self.model if (cur and self.model) else be.default_model
                hint = f"  ({shown})"
            label = Text()
            hue = _backend_choice_hex(name)
            label.append(name, style=f"bold {hue}" if hue else "")
            label.append(hint, style=_PAL["muted"])
            label.append(cur, style=_PAL["success"])
            label.append(avail, style=_PAL["muted"])
            opts.append((label, name))
        chosen = await self.push_screen_wait(Picker("switch backend", opts))
        if chosen:
            # picking an API backend with a curated catalog flows straight into
            # its model picker (Esc there keeps the recommended default)
            self._set_backend(chosen, after_model="PICK")

    @work
    async def action_pick_model(self):
        """Second level of /model: choose among the current backend's curated
        models. Free-form ids stay available via `/model <model-id>`."""
        name = self.backend or ""
        models = MODELS.models_for(name)
        if not models:
            self.notify(f"no curated models for {name or 'this backend'} — "
                        f"set one with `/model <model-id>`", severity="information")
            return
        be = BK.registry().get(name)
        current = self.model or (be.default_model if be is not None
                                 and isinstance(be, BK.OpenAICompatBackend) else None)
        opts = []
        for m in models:
            mark = "  ✓" if m.id == current else ""
            opts.append((f"{m.id:<26} · {m.note}{mark}", m.id))
        opts.append(("custom…  (set any id with `/model <model-id>`)", "__custom__"))
        chosen = await self.push_screen_wait(Picker(f"model for {name}", opts))
        if not chosen or chosen == "__custom__":
            # Esc / custom keeps the model the backend switch already applied (the
            # provider default). The switch itself stuck, so still offer to persist
            # it — the guard skips the prompt if it already equals the saved default.
            if chosen == "__custom__":
                self.notify("type `/model <model-id>` to set a custom model",
                            severity="information")
            self._offer_persist_default()
            return
        self._set_model(chosen)

    def _set_model(self, model_id, offer_default=True):
        """Switch the model on the CURRENT backend (session-scoped by default; the
        'make this your default?' prompt can persist it for new sessions)."""
        self.model = self.session.model = (model_id or "").strip() or None
        info = MODELS.find(self.backend or "", self.model or "")
        if info and "deprecated" in (info.note or ""):
            self.notify(f"model → {self.model} — {info.note}", severity="warning")
        else:
            self.notify(f"model → {self.model or '(backend default)'}",
                        severity="information")
        self._update_status()
        if offer_default:
            self._offer_persist_default()

    @work(exclusive=True, group="persist-default")
    async def _offer_persist_default(self):
        """After a user-initiated backend/model switch, offer to make it the
        default for FUTURE new cockpit sessions. 'Ask first', so a one-off override
        never silently rewrites ~/.cc-copilot.toml. Skipped when the choice already
        equals the saved default."""
        backend, model = self.backend, self.model
        if not backend:
            return
        if not os.path.isfile(OB.CFG.path()):
            return     # no config yet — use /init to set one up; a casual model
                       # switch shouldn't silently create a config file
        try:
            saved_b, saved_m = OB.saved_default()
        except Exception:
            saved_b, saved_m = "", ""
        if backend == saved_b and (model or "") == (saved_m or ""):
            return                                   # already the saved default
        label = backend + (f" · {model}" if model else "")
        chosen = await self.push_screen_wait(Picker(
            f"make {label} the default for new cockpit sessions?",
            [("Yes — every new cockpit starts here", "yes"),
             ("No — just this session", "no")]))
        if chosen != "yes":
            return
        try:
            OB.persist_default(backend, model)
        except OSError as e:
            self.notify(f"could not save default: {e}", severity="error")
            return
        self.notify(f"default → {label}", severity="information")

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

    def _set_backend(self, name, after_model=None, offer_default=True):
        """Switch backend. ``after_model``: None = land on the provider default;
        "PICK" = open the model picker after the switch (API + catalog only);
        any other string = set that model id after the switch. ``offer_default``
        gates the "make this your default?" prompt — off for onboarding / key
        capture, which already write the config."""
        try:
            be = BK.resolve(name)
        except BK.BackendError as e:
            self.notify(str(e), severity="error"); return
        # Landing on an API provider that still needs a key: capture it inline.
        # The picker can't carry a key, and resolve() succeeds without one (the
        # key is only checked at call time), so switching silently would leave
        # every chat failing with "set <PROVIDER>_API_KEY". Prompt instead.
        choice = OB.choice_for_or_none(name)
        if choice and choice.kind == "api" and not be.available():
            self.push_screen(KeyPrompt(choice),
                             lambda k: self._finish_api_switch(name, choice, k,
                                                               after_model))
            return
        self._commit_backend(name, be, after_model, offer_default=offer_default)

    def _commit_backend(self, name, be, after_model=None, offer_default=True):
        self.backend = self.session.backend = name
        # Keep the active model coherent with the new backend's kind: an API
        # backend uses its provider default (e.g. deepseek-v4-flash); a CLI
        # backend uses its own and must not inherit a stale API model —
        # otherwise a claude→deepseek→claude round-trip would run `claude
        # --model deepseek-v4-flash`. Mirrors the onboarding path (_after_onboard).
        if isinstance(be, BK.OpenAICompatBackend):
            self.model = self.session.model = be.default_model or self.model
        elif isinstance(be, BK.CliBackend):
            self.model = self.session.model = None
        self.notify(f"backend → {name}", severity="information")
        self._update_status()
        # Offer to persist the new default only once the choice has SETTLED: if we
        # chain into the model picker / a specific model, that terminal step makes
        # the offer instead (so the user isn't asked twice in one switch).
        chained = False
        if after_model and isinstance(be, BK.OpenAICompatBackend):
            if after_model == "PICK":
                if MODELS.models_for(name):
                    self.action_pick_model(); chained = True
            else:
                self._set_model(after_model, offer_default=offer_default); chained = True
        if not chained and offer_default:
            self._offer_persist_default()

    def _finish_api_switch(self, name, choice, key, after_model=None):
        """KeyPrompt callback: persist the entered key, then complete the switch.
        A None key means the user cancelled — keep the current backend."""
        if not key:
            self.notify(f"kept {self.backend or 'current'} backend — no key entered",
                        severity="warning")
            return
        # Save the model the user actually asked for — a specific id (from
        # `/model deepseek:deepseek-v4-pro`) rather than the provider default — so
        # the saved config matches the live session and the next cockpit starts on
        # it. ("PICK" / None mean "no specific model yet" → the provider default.)
        save_model = (after_model if after_model and after_model != "PICK"
                      else choice.default_model)
        try:
            OB.write_choice(name, model=save_model, key_value=key)
            OB.apply_to_env(name, model=save_model, key_value=key)
        except OSError as e:
            self.notify(f"could not save key: {e}", severity="error"); return
        # write_choice already persisted this backend+model as the default, so
        # don't also ask "make it your default?".
        self._commit_backend(name, BK.resolve(name), after_model, offer_default=False)
        self.notify(f"key saved · {choice.key_env}", severity="information")

    def action_copy_selection(self) -> None:
        """Ctrl+Y: copy the current text selection to the system clipboard. Textual's
        drag-select highlights message text in the app; this copies it as clean text
        — no chrome (role-bar / borders), works over tmux/SSH. Ctrl+C stays bound to
        quit, so it's never ambiguous."""
        try:
            text = self.screen.get_selected_text()
        except Exception:
            text = None
        if not text:
            self.notify("nothing selected — drag to select text, then Ctrl+Y",
                        severity="information")
            return
        self._put_on_clipboard(text)
        self.clear_selection()
        self._render_tip()                           # selection gone → drop the copy hint
        n = len(text)
        self.notify(f"copied {n} char{'' if n == 1 else 's'} to the clipboard")

    def _put_on_clipboard(self, text: str) -> None:
        """Copy ``text`` to the system clipboard robustly: OSC 52 (Textual) so it
        reaches the OUTER terminal over SSH/tmux, AND a local clipboard command —
        because OSC 52 silently no-ops in some terminals (notably macOS Terminal.app,
        per Textual's own copy_to_clipboard docs). Each covers what the other can't;
        both are best-effort."""
        try:
            self.copy_to_clipboard(text)                 # OSC 52 — remote / SSH / tmux
        except Exception:
            pass
        import shutil
        for argv in (["pbcopy"], ["wl-copy"],
                     ["xclip", "-selection", "clipboard"],
                     ["xsel", "--clipboard", "--input"], ["clip"]):
            if shutil.which(argv[0]):
                try:
                    subprocess.run(argv, input=text.encode("utf-8"), timeout=2,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                break                                    # first available tool wins

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
        self._chat_prompt_nav_index = None
        self._update_chat_pin()
        self.notify("view cleared (saved history kept — /forget to delete it)")

    def action_forget(self):
        # Delete THIS conversation's saved history from disk + clear the view.
        store = self.session.store
        # discard pending prompts FIRST — /forget means "drop this", and it must
        # do so even with history off (--no-persist), which returns early below.
        self._msg_queue.clear()
        if not store.enabled:
            self.notify("history is off — nothing saved to forget", severity="warning")
            return
        if self._busy and self._same_conv(self._answer_store, store):
            # an in-flight answer FOR THIS conversation must not keep painting
            # into the cleared pane or re-create the files we are about to
            # delete when it completes — abandon it (cancel the transport;
            # _answer_done drops it silently). An answer running for a
            # DIFFERENT conversation is left alone: forgetting B must not
            # discard A's unrelated turn.
            self._answer_abandoned = True
            h = self._answer_handle
            if h is not None:
                h.cancel()
        store.delete()
        self.session.history = []
        self._sync_prompt_history_from_session()
        chat = self.query_one("#chat", VerticalScroll)
        chat.remove_children()
        self._chat_prompt_nav_index = None
        chat.mount(self._role(
            Text("(forgot this conversation's saved history)", "dim"), "role-event"))
        self._update_chat_pin()
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
