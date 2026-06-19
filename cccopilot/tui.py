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
import base64
import random
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

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
               models as MODELS, scope_groups as SG)
from .chat import (_fmt_alert, _fmt_diff, _GLYPH, _dur,
                   _deterministic_goal, _goal_context_question)


_WATCH_DEFAULT_MICRO_INTERVAL = 30.0
_WATCH_DEFAULT_DIGEST_INTERVAL = 300.0
_WATCH_DEFAULT_DIGEST_EVENTS = 25
_WATCH_DEFAULT_HEARTBEAT_INTERVAL = 600.0
_WATCH_BUFFER_LIMIT = 24
_WATCH_RECENT_LIMIT = 8
_WATCH_STEP_LIMIT = 40
_WATCH_STEP_MIN_EVENTS = 5
_WATCH_STEP_MIN_SECONDS = 90.0
_WATCH_PRESET_INSTRUCTIONS = {
    "zh": ("中文", "Answer watch updates in Chinese."),
    "cn": ("中文", "Answer watch updates in Chinese."),
    "中文": ("中文", "Answer watch updates in Chinese."),
    "chinese": ("中文", "Answer watch updates in Chinese."),
    "in chinese": ("中文", "Answer watch updates in Chinese."),
    "en": ("English", "Answer watch updates in English."),
    "english": ("English", "Answer watch updates in English."),
    "in english": ("English", "Answer watch updates in English."),
}


def _watch_instruction(arg: str) -> tuple[str, str]:
    """Normalize a lightweight `/watch <preset>` steer."""
    raw = (arg or "").strip()
    if not raw:
        return "", ""
    label, instruction = _WATCH_PRESET_INSTRUCTIONS.get(raw.lower(), ("", ""))
    if instruction:
        return instruction, label
    return raw, _short_activity(raw, 24)


@dataclass
class _WatchStep:
    """One browsable monitor card in a `/watch` run."""

    id: int
    title: str
    phase: str
    trigger: str
    started_at: float
    target_key: str = ""
    target_label: str = ""
    ended_at: float = 0.0
    status: str = "active"
    current: str = ""
    summary: str = ""
    digest: str = ""
    attention: str = ""
    recent_updates: list[str] = field(default_factory=list)
    evidence_lines: list[str] = field(default_factory=list)
    event_count: int = 0


@dataclass
class _WatchTarget:
    """One transcript watched by a `/watch` run."""

    path: str
    session_id: str
    agent: str
    title: str = ""
    size: int = -1
    state: object = None


@dataclass
class _WatchRun:
    """Opt-in `/watch` state for one cockpit-visible monitoring run."""

    active: bool = False
    paused: bool = False
    pause_reason: str = ""
    started_at: float = 0.0
    last_micro_at: float = 0.0
    last_digest_at: float = 0.0
    last_alert_at: float = 0.0
    last_heartbeat_at: float = 0.0
    last_emit_at: float = 0.0
    last_summary: str = ""
    last_micro_text: str = ""
    last_digest_text: str = ""
    last_alert_text: str = ""
    last_digest_reason: str = ""
    scope_sig: object = None
    phase: str = ""
    last_phase: str = ""
    digest_buffer: list[str] = field(default_factory=list)
    recent_updates: list[str] = field(default_factory=list)
    events_since_digest: int = 0
    pending_narration: bool = False
    pending_digest_reason: str = ""
    phase_digest_pending: bool = False
    done_digest_pending: bool = False
    instruction: str = ""
    instruction_label: str = ""
    targets: dict = field(default_factory=dict)
    target_count: int = 0
    last_target_label: str = ""
    monitor_target_key: str = ""
    steps: list[_WatchStep] = field(default_factory=list)
    step_seq: int = 0
    step_index: int = -1
    follow_latest: bool = True
    unseen_steps: int = 0
    mode: str = "normal"
    micro_interval: float = _WATCH_DEFAULT_MICRO_INTERVAL
    digest_interval: float = _WATCH_DEFAULT_DIGEST_INTERVAL
    digest_events: int = _WATCH_DEFAULT_DIGEST_EVENTS
    heartbeat_interval: float = _WATCH_DEFAULT_HEARTBEAT_INTERVAL


def _parse_watch_step_decision(text: str) -> dict:
    out = {}
    for raw in str(text or "").splitlines():
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        key = k.strip().lower()
        if key in ("action", "title", "phase", "reason", "attention"):
            out[key] = " ".join(v.strip().split())
    action = out.get("action", "").lower()
    if action not in ("same", "new"):
        return {}
    out["action"] = action
    return out


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
    "type `@` for evidence scope (`@session`, `@sessions`, `@project`):\n"
    "  /observe /brief /check  attention · recap · safety (LLM-free)\n"
    "  /now [steer]            recommend the next step (e.g. /now in spanish; LLM)\n"
    "  /goal [steer]           draft a paste-ready agent /goal from context\n"
    "  /since [30m|1d] [--raw] [steer]  recap since you last looked (--raw = cited delta)\n"
    "  /handoff [file]         shareable Markdown handoff\n"
    "  /diff                   changes since last turn\n"
    "  /status                 fleet board — every session, neediest first\n"
    "  /watch [preset]         core observer loop (e.g. /watch 中文); /watch view monitor\n"
    "  /sessions  /here         choose evidence session(s) · watch your own live one\n"
    "  /scope save|load NAME    save or reuse a named evidence scope (@NAME)\n"
    "  /target                 show the current cockpit target (id · evidence · scope)\n"
    "  /resume                 resume a cockpit session\n"
    "  /new                    start a new cockpit session\n"
    "  /theme                  switch cockpit palette\n"
    "  /rewind                 fork the chat from an earlier message (Esc Esc on empty)\n"
    "  /model [name]           switch backend                     (Ctrl+T)\n"
    "  /init                   reopen the model picker (Claude / Codex / API key)\n"
    "  /stop                   interrupt answer; restore prompt for editing     (Ctrl+Z)\n"
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
    ("/goal", "draft a paste-ready agent /goal from agent + project context", False),
    ("/since", "recap since you last looked (30m / 2h / 1d; --raw = cited delta; trailing text steers it)", True),
    ("/handoff", "shareable Markdown handoff (brief + what changed)", True),
    ("/brief", "evidence-cited recap (LLM-free)", False),
    ("/check", "safety / off-track verdict (LLM-free)", False),
    ("/diff", "what changed since your last turn", False),
    ("/status", "fleet board — every session in this project, neediest first", False),
    ("/watch", "core observer loop; add preset like 中文/english; /watch view monitor", True),
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
    ("/stop", "interrupt answer and restore its prompt for editing (Ctrl+Z)", False),
    ("/forget", "delete THIS cockpit session's saved resume state", False),
    ("/clear", "clear the chat view (keeps saved history)", False),
    ("/help", "show help", False),
    ("/quit", "exit the cockpit", False),
]


_SCOPE_MENTIONS = [
    ("@session", "current attached session only"),
    ("@sessions", "choose one or more evidence sessions"),
    ("@project", "all live sessions plus project context"),
]


def _osc52_sequence(text: str) -> str:
    payload = base64.b64encode(str(text or "").encode("utf-8")).decode("ascii")
    return f"\x1b]52;c;{payload}\a"


def _tmux_passthrough(seq: str) -> str:
    # tmux DCS passthrough: ESC P tmux; <inner escapes doubled> ESC \
    return "\x1bPtmux;" + str(seq or "").replace("\x1b", "\x1b\x1b") + "\x1b\\"
_ARG_CMDS = {c for c, _, takes in _SLASH_CMDS if takes}

# Rotating feature tips shown subtly in the composer chrome (see _rotate_tip).
# They carry the discoverability the slimmed-down footer no longer shows —
# ordered from "most useful when you just got back" down to niche keys. Each is
# one line, <=64 chars so it survives a narrow sidebar. Curated from the core
# feature set.
_TIPS = [
    "/since recaps what the agent did while you were away",
    "Re-entry greets you: N new since you last looked",
    "Every recap line cites a transcript line [L#] — never guessed",
    "/check tells you if it's safe to continue — off-track signals",
    "/handoff writes a shareable Markdown brief of what changed",
    "/observe surfaces the next human decision waiting on you",
    "/brief recaps with sources, no LLM, no guessing",
    "/diff shows what changed since your last turn",
    "/watch observes long runs; try /watch 中文, then /watch view",
    "Read-only: the cockpit never writes to the agent or transcript",
    "Type @ to switch evidence: @session, @sessions, @project",
    "/sessions picks which session(s) the cockpit watches",
    "/use <n|id> switches the watched session by number or id",
    "/here watches your OWN current live session",
    "/scope multi or project widens the evidence across sessions",
    "One cockpit watches Claude AND Codex at once, by project",
    "Empty input: Tab switches multi-session activity tabs",
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
                  else S.cached_build(ref.path, SRC.parse))
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


def _scoped_session_timeline_header(ref, st, a, index: int, total: int) -> Text:
    ag = getattr(ref, "agent", "claude")
    title = _short_activity(_session_title(st, ref), 52)
    t = Text()
    t.append(f"session {index}/{total} · ", style=_PAL["muted"])
    t.append(f"{ag} session", style=_agent_hex(ag))
    if title and title != "(untitled)":
        t.append(f" · {title}", style=_PAL["text"])
    if st is not None:
        t.append(f" · safety {getattr(a, 'verdict', '?')}", style=_VERDICT_HEX.get(
            getattr(a, "verdict", ""), _PAL["muted"]))
    return t


def _append_attached_chip(t: Text, agent: str, sid: str, title: str = "") -> None:
    """Append one compact agent/session chip for the prompt-area attached HUD."""
    ag = (agent or "claude").strip().lower()
    title = _short_activity(title, 32)
    t.append("↳ ", style=_PAL["muted"])
    t.append(f"{ag} session", style=_agent_hex(ag))
    if title and title != "(untitled)":
        t.append(f" {title}", style=_PAL["text"])
    if sid:
        t.append(f" · {sid}", style=_PAL["muted"])


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

    def _prompt_history_nav(self, event: events.Key, app) -> bool:
        if event.key not in ("up", "down"):
            return False
        lines = (self.text or "").splitlines() or [""]
        row = self._cursor_row()
        at_history_edge = ("\n" not in self.text
                           or (event.key == "up" and row <= 0)
                           or (event.key == "down" and row >= len(lines) - 1))
        if not at_history_edge:
            return False
        fn = getattr(app, "_prompt_history_prev" if event.key == "up"
                     else "_prompt_history_next", None)
        replacement = fn(self.text) if callable(fn) else None
        if replacement is None:
            return False
        event.prevent_default()
        event.stop()
        hide = getattr(app, "_slash_hide", None)
        if callable(hide):
            hide()
        hide = getattr(app, "_mention_hide", None)
        if callable(hide):
            hide()
        self._replace_text(replacement)
        return True

    async def _on_key(self, event: events.Key) -> None:
        app = self.app
        if (getattr(app, "_prompt_history_index", None) is not None
                and self._prompt_history_nav(event, app)):
            return

        # When the `/` suggestion popup is open, the arrow/Tab/Esc keys drive it
        # instead of the text cursor.
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

        if getattr(app, "_mention_open", False):
            if event.key == "down":
                event.prevent_default(); event.stop(); app._mention_move(1); return
            if event.key == "up":
                event.prevent_default(); event.stop(); app._mention_move(-1); return
            if event.key == "tab":
                event.prevent_default(); event.stop(); app._mention_complete(); return
            if event.key == "enter":
                event.prevent_default(); event.stop(); app._mention_accept(); return
            if event.key == "escape":
                event.prevent_default(); event.stop(); app._mention_hide(); return

        if self._prompt_history_nav(event, app):
            return

        if event.key in ("tab", "shift+tab") and not self.text:
            delta = -1 if event.key == "shift+tab" else 1
            nav = getattr(app, "_watch_monitor_target_nav", None)
            if callable(nav) and nav(delta):
                event.prevent_default()
                event.stop()
                return
            nav = getattr(app, "_activity_target_nav", None)
            if callable(nav) and nav(delta):
                event.prevent_default()
                event.stop()
                return

        if event.key in ("left", "right") and not self.text:
            nav = getattr(app, "_watch_monitor_step_nav", None)
            if callable(nav) and nav(-1 if event.key == "left" else 1):
                event.prevent_default()
                event.stop()
                return
            fn = getattr(app, "_jump_chat_prompt", None)
            if callable(fn) and fn(-1 if event.key == "left" else 1):
                event.prevent_default()
                event.stop()
                return

        # Esc clears the current draft. On an already-empty composer, a quick
        # second Esc opens rewind; the first tap only primes it.
        if event.key == "escape":
            event.prevent_default(); event.stop()
            close_monitor = getattr(app, "_close_watch_monitor_if_open", None)
            if callable(close_monitor) and close_monitor():
                return
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
    /* rotating feature tip — one subtle muted line on the flat ground, above the
       prompt-area attached-session HUD. height:1 + no wrap so a long tip clips
       instead of growing the row and stealing space from the chat. */
    #tip { height: 1; padding: 0 1; color: $text-muted; text-wrap: nowrap; }
    #session-hud {
        height: 1; min-height: 1; max-height: 1;
        background: $boost; color: $text; padding: 0 1; text-wrap: nowrap;
    }
    #composer {
        height: auto; min-height: 3; max-height: 8;
        border: round $accent; padding: 0 1; margin: 0 1;
        background: $surface;
    }
    #composer:focus-within { border: round $primary; }
    #watch-dock {
        height: 1; min-height: 1;
        background: $boost; color: $text; padding: 0 1; text-wrap: nowrap;
    }
    #watch-dock:hover { background: $accent 20%; }
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
    #watch-monitor {
        width: 100%; height: 1fr; background: $panel; padding: 0 0 0 1;
        scrollbar-size-vertical: 1;
    }
    #watch-monitor-title {
        height: auto; min-height: 2;
        margin: 0 0 1 0;
        color: $accent; text-style: bold;
    }
    #watch-monitor-phase {
        height: auto; min-height: 3;
        margin: 0 0 1 0;
        color: $primary;
    }
    #watch-monitor-now,
    #watch-monitor-digest,
    #watch-monitor-alert {
        height: auto; min-height: 3; max-height: 8;
        margin: 0 0 1 0;
    }
    #watch-monitor-recent {
        height: auto; min-height: 4;
        margin: 0 0 1 0;
        color: $text-muted;
    }
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
    # prompt controls (see _TIPS / _rotate_tip), which is where discovery now
    # lives.
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
        self._answer_stop_reverted = False  # stopped turn already rolled back in UI
        self._answer_prompt_widget = None   # live prompt bubble for in-flight answer
        self._answer_scope_marker_widget = None
        self._answer_prompt_text = ""
        self._answer_prompt_history_added = False
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
        self._mention_open = False
        self._watch_stop = threading.Event()
        self._watch_path = None
        self._watch_size = -1
        self._watch_state = None
        self._watch_worker_started = False
        self._watch_run = _WatchRun()
        self._watch_mode = False
        self._watch_started_at = 0.0
        self._watch_last_emit_at = 0.0
        self._watch_last_summary = ""
        self._watch_narrating = False
        self._watch_monitor_open = False
        self._watch_chat_widgets = []
        self._watch_monitor_render_sig = None
        self._scoped_timeline_rebuild_at = 0.0
        self._timeline_target_key = "all"
        self._timeline_target_order = []
        self._timeline_sig = None       # evidence identity of the last rebuild

    @property
    def _watch_mode(self) -> bool:
        return self._watch_run.active

    @_watch_mode.setter
    def _watch_mode(self, value: bool) -> None:
        self._watch_run.active = bool(value)
        if not value:
            self._watch_run.paused = False
            self._watch_run.pause_reason = ""

    @property
    def _watch_started_at(self) -> float:
        return self._watch_run.started_at

    @_watch_started_at.setter
    def _watch_started_at(self, value: float) -> None:
        self._watch_run.started_at = float(value or 0.0)

    @property
    def _watch_last_emit_at(self) -> float:
        return self._watch_run.last_emit_at

    @_watch_last_emit_at.setter
    def _watch_last_emit_at(self, value: float) -> None:
        self._watch_run.last_emit_at = float(value or 0.0)

    @property
    def _watch_last_summary(self) -> str:
        return self._watch_run.last_summary

    @_watch_last_summary.setter
    def _watch_last_summary(self, value: str) -> None:
        self._watch_run.last_summary = str(value or "")

    @property
    def _watch_narrating(self) -> bool:
        return self._watch_run.pending_narration

    @_watch_narrating.setter
    def _watch_narrating(self, value: bool) -> None:
        self._watch_run.pending_narration = bool(value)

    # ---- prompt history (composer ↑/↓, terminal-style) ----
    def _prompt_history_from_session(self) -> list:
        return [text for role, text in getattr(self.session, "history", [])
                if role == "user" and str(text or "").strip()]

    def _sync_prompt_history_from_session(self) -> None:
        self._prompt_history = self._prompt_history_from_session()
        self._reset_prompt_history_nav()

    def _remember_prompt(self, text: str) -> bool:
        text = str(text or "").strip()
        added = False
        if text and (not self._prompt_history or self._prompt_history[-1] != text):
            self._prompt_history.append(text)
            added = True
        self._reset_prompt_history_nav()
        self._cancel_rewind_esc()
        return added

    def _rollback_prompt_history(self, text: str, added: bool) -> None:
        """Undo a prompt-history append for a turn that never actually landed."""
        text = str(text or "").strip()
        if added and self._prompt_history and self._prompt_history[-1] == text:
            self._prompt_history.pop()
        self._reset_prompt_history_nav()

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
        watch_monitor = VerticalScroll(id="watch-monitor")
        watch_monitor.display = False
        session_hud = Static("", id="session-hud")
        watch_dock = Static("", id="watch-dock")
        # The timeline and chat are display-only. Keep them out of the focus
        # chain so a click (or Tab) can never strand focus on a scroll pane —
        # that used to leave typed / IME (e.g. Chinese) input with no target.
        # Mouse-wheel scrolling still works without focus.
        header.can_focus = False
        timeline.can_focus = False
        chat_pin.can_focus = False
        chat.can_focus = False
        watch_monitor.can_focus = False
        session_hud.can_focus = False
        watch_dock.can_focus = False
        yield header
        yield timeline
        yield chat_pin
        yield chat
        with watch_monitor:
            yield Static("", id="watch-monitor-title", classes="role-event")
            yield Static("", id="watch-monitor-phase", classes="role-event")
            yield Static("", id="watch-monitor-now", classes="role-assistant")
            yield Static("", id="watch-monitor-digest", classes="role-event")
            yield Static("", id="watch-monitor-alert", classes="role-alert")
            yield Static("", id="watch-monitor-recent", classes="role-event")
        yield Static("", id="status")
        yield Static("", id="tip")              # rotating feature tip (subtle)
        yield session_hud
        slash = OptionList(id="slash")          # `/` command autocomplete
        slash.can_focus = False
        slash.display = False
        yield slash
        yield Composer(id="composer")
        yield watch_dock
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
            self._ensure_watch_worker()
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
            room = self.size.height - 13   # header+status+tip+attached+composer+watch+footer+min chat
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
        elif getattr(widget, "id", "") == "watch-dock":
            if not self._watch_mode or self._watch_run.paused:
                self.action_watch("")
            else:
                self.action_watch("view")
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
        if getattr(self, "_prompt_history_index", None) is not None:
            self._slash_open = False
            self._mention_open = False
            ol.display = False
            return
        mention = re.fullmatch(r"@[\w.-]*", text)
        needle = text.lower()
        mention_matches = [(c, d) for c, d in self._scope_mention_options()
                           if c.lower().startswith(needle)] if mention else []
        if mention_matches:
            self._slash_open = False
            self._mention_open = True
            ol.clear_options()
            for c, d in mention_matches:
                label = Text(c, style=f"bold {_PAL['accent']}")
                label.append(f"   {d}", style=_PAL["muted"])
                ol.add_option(Option(label, id=c))
            ol.display = True
            ol.highlighted = 0
            return
        self._mention_open = False
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
        self._mention_open = False
        ol.highlighted = 0

    def _suggest_move(self, delta) -> None:
        ol = self.query_one("#slash", OptionList)
        if ol.option_count:
            ol.highlighted = max(0, min(ol.option_count - 1, (ol.highlighted or 0) + delta))

    def _slash_move(self, delta) -> None:
        self._suggest_move(delta)

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
        self._mention_open = False
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

    def _mention_move(self, delta) -> None:
        self._suggest_move(delta)

    def _scope_mention_options(self) -> list:
        opts = list(_SCOPE_MENTIONS)
        try:
            for g in SG.list_groups():
                label = f"@{g.name}"
                detail = f"saved scope: {g.scope}"
                if g.scope_sessions:
                    detail += f":{len(g.scope_sessions)}"
                opts.append((label, detail))
        except Exception:
            pass
        return opts

    def _mention_hide(self) -> None:
        self._mention_open = False
        try:
            self.query_one("#slash", OptionList).display = False
        except Exception:
            pass

    def _mention_complete(self) -> None:
        ol = self.query_one("#slash", OptionList)
        if not ol.option_count:
            return
        comp = self.query_one("#composer", Composer)
        comp.text = ol.get_option_at_index(ol.highlighted or 0).id
        comp.move_cursor(comp.document.end)
        self._slash_open = False
        self._mention_hide()
        comp.focus()

    def _mention_accept(self) -> None:
        ol = self.query_one("#slash", OptionList)
        if not ol.option_count:
            return
        self._mention_apply(ol.get_option_at_index(ol.highlighted or 0).id)

    def _mention_apply(self, token: str) -> bool:
        comp = self.query_one("#composer", Composer)
        comp.text = ""
        self._slash_open = False
        self._mention_hide()
        comp.focus()
        return self._apply_scope_mention(token)

    def _apply_scope_mention(self, token: str) -> bool:
        raw = str(token or "").strip()
        key = raw.lower()
        if key == "@sessions":
            self.action_sessions()
            return True
        scope = {"@session": SC.SESSION, "@project": SC.PROJECT}.get(key)
        if scope:
            out = self.session.meta(f"/scope {scope}")
        elif key.startswith("@"):
            out = self.session.meta(f"/scope load {raw[1:]}")
            if not str(out).startswith("scope group "):
                self.notify(str(out).splitlines()[0], severity="warning")
                return True
        else:
            return False
        self._watch_scope_changed("evidence scope changed")
        self.sub_title = _sub_title(self.session)
        self._refresh_scope_view()
        self.notify(str(out).splitlines()[0], severity="information")
        return True

    @on(OptionList.OptionSelected, "#slash")
    def _slash_pick(self, event) -> None:
        if self._mention_open or str(event.option.id or "").startswith("@"):
            self._mention_apply(event.option.id)
        else:
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
        yield SystemCommand("Watch", "Start the core observer loop",
                            self.action_watch)
        yield SystemCommand("Watch Monitor", "Open the in-place watch monitor",
                            lambda: self.action_watch("view"))
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

    def _answer_scope_label(self) -> str:
        scope = getattr(self.session, "scope", SC.SESSION)
        selectors = list(getattr(self.session, "scope_sessions", None) or [])
        if scope == SC.SESSION:
            return "session"
        if scope == SC.MULTI:
            count = len(selectors)
            if not count:
                try:
                    count = len(SC.resolve_session_refs(self.session.path, []))
                except Exception:
                    count = 0
            return f"sessions:{count}" if count else "sessions"
        if scope == SC.PROJECT:
            return f"project:{len(selectors)}" if selectors else "project"
        return str(scope or "session")

    def _make_answer_scope_widget(self):
        t = Text("evidence · ", style=_PAL["muted"])
        label = self._answer_scope_label()
        t.append(label, style=_PAL["accent"])
        if getattr(self.session, "scope", SC.SESSION) != SC.SESSION:
            t.append(" · ", style=_PAL["muted"])
            t.append("read-only scope for this answer", style=_PAL["muted"])
        return self._role(t, "role-event")

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
        if self._watch_monitor_open:
            pin.update(self._watch_monitor_menu_text())
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
        if self._watch_monitor_open:
            self._update_watch_monitor()
            return
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

    def _watch_chat(self, widget):
        try:
            widget._cc_watch_ephemeral = True
        except Exception:
            pass
        self._watch_chat_widgets.append(widget)
        self._chat(widget)

    def _clear_watch_chat_ephemeral(self) -> None:
        kept = []
        for widget in self._watch_chat_widgets:
            if not getattr(widget, "_cc_watch_ephemeral", False):
                kept.append(widget)
                continue
            attached = bool(getattr(widget, "is_attached", False)
                            or getattr(widget, "parent", None) is not None)
            if not attached:
                continue
            try:
                widget.remove()
            except Exception:
                kept.append(widget)
        self._watch_chat_widgets = kept
        self._update_chat_pin()

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

    @staticmethod
    def _timeline_key_for_ref(ref) -> str:
        return os.path.abspath(getattr(ref, "path", "") or "")

    def _timeline_target_items(self, snap=None) -> list:
        if self.session.scope == SC.SESSION:
            return []
        snap = _scope_snapshot(self.session) if snap is None else snap
        return list(snap.get("items", []) or [])

    def _timeline_target_keys(self, snap=None) -> list:
        items = self._timeline_target_items(snap)
        if len(items) <= 1:
            self._timeline_target_order = []
            return []
        paths = [self._timeline_key_for_ref(ref) for ref, _st, _a in items]
        order = [p for p in self._timeline_target_order if p in paths]
        for p in paths:
            if p not in order:
                order.append(p)
        self._timeline_target_order = order
        return ["all"] + order

    def _ensure_timeline_target(self, snap=None) -> str:
        keys = self._timeline_target_keys(snap)
        if not keys:
            self._timeline_target_key = "all"
            return ""
        if self._timeline_target_key not in keys:
            self._timeline_target_key = "all"
        return self._timeline_target_key

    def _timeline_target_item(self, snap=None, key: str = ""):
        key = key or self._timeline_target_key
        by_path = {
            self._timeline_key_for_ref(ref): (ref, st, a)
            for ref, st, a in self._timeline_target_items(snap)
        }
        for idx, path in enumerate(self._timeline_target_keys(snap)[1:], start=1):
            if path == key and path in by_path:
                ref, st, a = by_path[path]
                return idx, ref, st, a
        return None

    def _timeline_title(self, snap=None, target_key: str = "") -> Text:
        snap = _scope_snapshot(self.session) if snap is None else snap
        target_key = target_key or self._ensure_timeline_target(snap)
        base = _scope_activity_title(self.session, snap)
        if self.session.scope == SC.SESSION:
            return Text(base, style=f"bold {_PAL['accent']}")
        items = self._timeline_target_items(snap)
        total = len(items)
        t = Text()
        t.append(base, style=f"bold {_PAL['accent']}")
        if not total:
            return t
        t.append(" · ", style=_PAL["muted"])
        if target_key == "all":
            t.append(f"all {total}", style=_PAL["secondary"])
        else:
            item = self._timeline_target_item(snap, target_key)
            if item is not None:
                idx, ref, st, _a = item
                title = _short_activity(_session_title(st, ref), 38)
                t.append(f"session {idx}/{total} ", style=_PAL["secondary"])
                t.append(title if title != "(untitled)" else "untitled",
                         style=_agent_hex(getattr(ref, "agent", "claude")))
        if total > 1:
            t.append(" · Tab activity", style=_PAL["muted"])
        return t

    def _activity_target_nav(self, delta: int) -> bool:
        if self._watch_monitor_open:
            return False
        snap = _scope_snapshot(self.session)
        keys = self._timeline_target_keys(snap)
        if len(keys) <= 1:
            return False
        cur = self._ensure_timeline_target(snap)
        try:
            i = keys.index(cur)
        except ValueError:
            i = 0
        self._timeline_target_key = keys[(i + int(delta or 0)) % len(keys)]
        self._rebuild_timeline()
        self._update_header()
        self._update_status()
        return True

    def _evidence_sig(self, target_key=None):
        """Identity of *what* the timeline is showing — scope, the session, and the
        multi-session set. A rebuild whose signature is unchanged is a same-session
        refresh (poll tick, theme, /refresh, re-observe, a no-op /scope); a changed
        signature is an evidence switch (/sessions, /use, /here, /scope, /resume)."""
        s = self.session
        target = self._timeline_target_key if target_key is None else target_key
        return (s.scope, s.path,
                tuple(sorted(str(x) for x in (getattr(s, "scope_sessions", None) or []))),
                target)

    def _rebuild_timeline(self):
        rl = self.query_one("#timeline-log", RichLog)
        prev_y = rl.scroll_offset.y
        # "at the bottom" is EXACT here — NOT the append path's `- 1` slack.
        # When the log overflows by a single line (max_scroll_y == 1) that slack
        # would treat a top reader (y == 0) as at-bottom and yank them down.
        was_bottom = prev_y >= rl.max_scroll_y         # capture BEFORE clear
        snap = _scope_snapshot(self.session)
        target_key = self._ensure_timeline_target(snap)
        # Keep the reader's scroll only when the evidence is unchanged; an evidence
        # switch (or the first build) lands on the newest line. Derived, not passed
        # by callers — _refresh_scope_view has both same-evidence and switch callers.
        sig = self._evidence_sig(target_key)
        keep_scroll = (sig == self._timeline_sig)
        self._timeline_sig = sig
        title = self._timeline_title(snap, target_key)
        try:
            self.query_one("#timeline-title", Static).update(title)
        except NoMatches:
            pass
        rl.clear()
        if self.session.scope == SC.SESSION:
            for level, line in O.timeline_lines(
                    self.session.path, self.session.st, self.session.scope,
                    sessions=self.session.scope_sessions, limit=2):
                self._timeline(_observer_timeline_line(level, line),
                               "role-alert" if level == "alarm"
                               else "role-warn" if level == "warn" else "role-event",
                               follow=False)
            self._timeline(_timeline_status_line(self.session.st), follow=False)
            agent_hex = _agent_hex(_agent_of(self.session))
            for line in _recent_activity_lines(self.session.st, agent_hex=agent_hex):  # the *entire* history
                self._timeline(line, follow=False)
            self._land_timeline(rl, prev_y, was_bottom, keep_scroll)
            return
        if target_key and target_key != "all":
            item = self._timeline_target_item(snap, target_key)
            if item is not None:
                idx, ref, st, a = item
                for level, line in O.timeline_lines(ref.path, st, SC.SESSION,
                                                    sessions=[], limit=2):
                    self._timeline(_observer_timeline_line(level, line),
                                   "role-alert" if level == "alarm"
                                   else "role-warn" if level == "warn" else "role-event",
                                   follow=False)
                total = len(self._timeline_target_items(snap))
                self._timeline(_scoped_session_timeline_header(ref, st, a, idx, total),
                               follow=False)
                self._timeline(_timeline_status_line(st), follow=False)
                agent_hex = _agent_hex(getattr(ref, "agent", "claude"))
                for line in _recent_activity_lines(st, agent_hex=agent_hex):
                    self._timeline(line, follow=False)
                self._land_timeline(rl, prev_y, was_bottom, keep_scroll)
                self._scoped_timeline_rebuild_at = time.monotonic()
                return
        for level, line in O.timeline_lines(
                self.session.path, self.session.st, self.session.scope,
                sessions=self.session.scope_sessions, limit=2):
            self._timeline(_observer_timeline_line(level, line),
                           "role-alert" if level == "alarm"
                           else "role-warn" if level == "warn" else "role-event",
                           follow=False)
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
        self._scoped_timeline_rebuild_at = time.monotonic()

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

    def _session_hud(self):
        try:
            return self.query_one("#session-hud", Static)
        except Exception:
            return None

    def _watch_dock(self):
        try:
            return self.query_one("#watch-dock", Static)
        except Exception:
            return None

    def _watch_monitor(self):
        try:
            return self.query_one("#watch-monitor", VerticalScroll)
        except Exception:
            return None

    def _header_text(self, snap=None) -> Text:
        root = _project_cwd(self.session)
        project = os.path.basename(root.rstrip(os.sep)) or root
        branch, changed = _git_summary(root)
        snap = _scope_snapshot(self.session) if snap is None else snap
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
            if title and title != "(untitled)":
                t.append(f" {title}", style=_PAL["text"])
            t.append(f" · {sid}", style=_PAL["muted"])
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

    def _session_hud_text(self, snap=None) -> Text:
        """Compact attached-session summary directly above the prompt box."""
        snap = _scope_snapshot(self.session) if snap is None else snap
        st = self.session.st
        t = Text()
        if self.session.scope == SC.SESSION:
            ag = _agent_of(self.session)
            sid = _sid(st=st, path=self.session.path)
            t.append("attached session", style=_PAL["muted"])
            t.append(" · ", style=_PAL["muted"])
            _append_attached_chip(t, ag, sid, _session_title(st) if st is not None else "")
            if st is None:
                t.append(" · history-only · transcript gone", style=_PAL["warning"])
                return t
            return t

        items = snap.get("items", [])
        t.append("attached sessions", style=_PAL["muted"])
        t.append(" · ", style=_PAL["muted"])
        t.append(self.session.scope, style=_PAL["accent"])
        t.append(f" · {_selection_label(self.session, snap)}", style=_PAL["text"])
        if snap.get("error"):
            t.append(f" · {snap['error']}", style=_PAL["warning"])
            return t
        if not items:
            t.append(" · no live evidence sessions", style=_PAL["warning"])
            return t
        for ref, rst, _a in items[:3]:
            t.append(" · ", style=_PAL["muted"])
            _append_attached_chip(t, getattr(ref, "agent", "claude"),
                                  _sid(ref=ref, st=rst), _session_title(rst, ref))
        extra = len(items) - 3
        if extra > 0:
            t.append(f" · +{extra} more", style=_PAL["muted"])
        return t

    def _watch_dock_text(self) -> Text:
        r = self._watch_run
        t = Text()
        t.append("watch", style=_PAL["muted"])
        if not r.active:
            t.append(" · off", style=_PAL["muted"])
            t.append(" · click start", style=_PAL["accent"])
            return t
        if r.paused:
            t.append(" · paused", style=_PAL["warning"])
            t.append(" · click resume", style=_PAL["accent"])
            return t
        phase = r.phase or (
            "watching-sessions" if self.session.scope != SC.SESSION
            else self._watch_phase(self.session.st)
        )
        t.append(" · on", style=_PAL["accent"])
        t.append(f" · {phase}", style=_PAL["primary"])
        if self.session.scope != SC.SESSION and r.target_count:
            t.append(f" · {r.target_count} sessions", style=_PAL["muted"])
        if r.instruction_label:
            t.append(f" · {r.instruction_label}", style=_PAL["secondary"])
        if r.pending_digest_reason:
            t.append(" · digest queued", style=_PAL["muted"])
        if r.last_alert_text:
            t.append(" · attention", style=_PAL["warning"])
        t.append(" · monitor open" if self._watch_monitor_open else " · monitor",
                 style=_PAL["accent"])
        return t

    def _watch_monitor_menu_text(self) -> Text:
        step = self._watch_selected_step()
        steps = self._watch_filtered_steps()
        count = len(steps)
        idx = (self._watch_run.step_index + 1) if step is not None else 0
        t = Text()
        t.append("watch monitor", style=f"bold {_PAL['accent']}")
        keys = self._watch_monitor_target_keys()
        if keys:
            cur = self._watch_ensure_monitor_target()
            target = self._watch_run.targets.get(cur)
            label = (self._watch_target_display(target)
                     if target is not None else "session")
            sty = self._watch_target_style(target)
            t.append(" · session ", style=_PAL["muted"])
            t.append(f"{keys.index(cur) + 1}/{len(keys)}", style=_PAL["secondary"])
            t.append(f" {label}", style=sty)
        if count:
            t.append(f" · step {idx}/{count}", style=_PAL["muted"])
            t.append(" · latest" if self._watch_run.follow_latest else " · history",
                     style=_PAL["secondary"] if self._watch_run.follow_latest else _PAL["warning"])
        if self._watch_run.unseen_steps and not self._watch_run.follow_latest:
            t.append(f" · {self._watch_run.unseen_steps} new", style=_PAL["warning"])
        t.append(" · ", style=_PAL["muted"])
        t.append("Esc", style=f"bold {_PAL['primary']}")
        t.append(" return · ", style=_PAL["muted"])
        t.append("←/→", style=f"bold {_PAL['primary']}")
        t.append(" steps · ", style=_PAL["muted"])
        if keys:
            t.append("Tab", style=f"bold {_PAL['primary']}")
            t.append(" sessions · ", style=_PAL["muted"])
        t.append("/watch refresh", style=_PAL["secondary"])
        t.append(" · ", style=_PAL["muted"])
        t.append("/watch stop", style=_PAL["warning"])
        t.append(" · Shift+Up/Down activity", style=_PAL["muted"])
        return t

    def _watch_section(self, title: str, body: str, title_style=None,
                       body_style=None) -> Text:
        t = Text()
        t.append(title.upper(), style=title_style or f"bold {_PAL['accent']}")
        t.append("\n")
        t.append(body or "none", style=body_style or _PAL["text"])
        return t

    def _watch_monitor_sections(self) -> dict:
        r = self._watch_run
        now = time.monotonic()
        state = "paused" if r.active and r.paused else ("on" if r.active else "off")
        elapsed = _dur(now - r.started_at) if r.active and r.started_at else "0s"
        step = self._watch_selected_step()
        steps = self._watch_filtered_steps()
        count = len(steps)
        idx = (r.step_index + 1) if step is not None else 0
        keys = self._watch_monitor_target_keys()
        cur_key = self._watch_ensure_monitor_target() if keys else ""
        cur_target = self._watch_run.targets.get(cur_key) if cur_key else None
        title = Text()
        title.append("watch monitor", style=f"bold {_PAL['accent']}")
        title.append(f" · {state}", style=_PAL["warning"] if r.paused else _PAL["accent"])
        if cur_target is not None:
            title.append(f" · session {keys.index(cur_key) + 1}/{len(keys)}",
                         style=_PAL["secondary"])
            title.append(" · ", style=_PAL["muted"])
            title.append(self._watch_target_display(cur_target),
                         style=self._watch_target_style(cur_target))
        if step is not None:
            title.append(f" · step {idx}/{count}", style=_PAL["secondary"])
            title.append(" · latest" if r.follow_latest else " · history",
                         style=_PAL["muted"])
        if cur_target is None:
            title.append(" · ", style=_PAL["muted"])
            title.append(self._watch_target_display(), style=self._watch_target_style())
            title.append(f" · {elapsed}", style=_PAL["muted"])
        else:
            title.append(f" · {elapsed}", style=_PAL["muted"])
        if r.instruction_label:
            title.append(f" · {r.instruction_label}", style=_PAL["secondary"])
        title.append("\n")
        title.append("read-only observer loop · session activity stays above",
                     style=_PAL["muted"])

        phase = (step.phase if step is not None else "") or r.phase or self._watch_phase(self.session.st)
        if not r.active and step is None:
            phase_body = "off · run /watch or click the dock to start"
        elif r.paused:
            phase_body = f"paused · {r.pause_reason or 'scope paused'}"
        elif step is not None:
            duration = _dur((step.ended_at or now) - step.started_at)
            phase_body = f"{step.title} · {phase} · {step.status} · {duration}"
        else:
            phase_body = phase

        current = (step.summary or step.current) if step is not None else ""
        current = current or r.last_micro_text or self._watch_current_activity()
        digest = (step.digest if step is not None else "")
        if not digest and not keys:
            digest = r.last_digest_text
        digest = digest or (
            "waiting for enough evidence on this session; digest runs automatically "
            "on phase changes, event thresholds, completion, or cadence")
        alert = (step.attention if step is not None else "")
        if not alert and not keys:
            alert = r.last_alert_text
        alert = alert or "none"
        if r.pending_digest_reason:
            digest += f"\nqueued: {r.pending_digest_reason}"

        if step is not None:
            recent = (step.recent_updates + step.evidence_lines)[-_WATCH_RECENT_LIMIT:]
        else:
            recent = r.recent_updates[-_WATCH_RECENT_LIMIT:]
        recent_body = "\n".join(recent) if recent else "no watch updates yet"

        return {
            "watch-monitor-title": title,
            "watch-monitor-phase": self._watch_section("phase", phase_body,
                                                       body_style=_PAL["primary"]),
            "watch-monitor-now": self._watch_section("now", current),
            "watch-monitor-digest": self._watch_section("auto digest", digest),
            "watch-monitor-alert": self._watch_section(
                "attention", alert, body_style=(_PAL["error"] if r.last_alert_text else _PAL["muted"])),
            "watch-monitor-recent": self._watch_section("recent", recent_body,
                                                        body_style=_PAL["muted"]),
        }

    def _watch_monitor_step_nav(self, delta: int) -> bool:
        if not self._watch_monitor_open:
            return False
        r = self._watch_run
        steps = self._watch_filtered_steps()
        if not steps:
            self._update_watch_monitor()
            return True
        latest = len(steps) - 1
        cur = latest if r.follow_latest or r.step_index < 0 else r.step_index
        nxt = max(0, min(latest, cur + int(delta or 0)))
        r.step_index = nxt
        if nxt >= latest:
            r.follow_latest = True
            r.unseen_steps = 0
        else:
            r.follow_latest = False
        self._update_watch_monitor()
        self._update_watch_dock()
        return True

    def _watch_monitor_target_nav(self, delta: int) -> bool:
        if not self._watch_monitor_open:
            return False
        keys = self._watch_monitor_target_keys()
        if len(keys) <= 1:
            self._update_watch_monitor()
            return bool(keys)
        cur = self._watch_ensure_monitor_target()
        try:
            i = keys.index(cur)
        except ValueError:
            i = 0
        self._watch_run.monitor_target_key = keys[(i + int(delta or 0)) % len(keys)]
        self._watch_run.follow_latest = True
        self._watch_run.unseen_steps = 0
        steps = self._watch_filtered_steps()
        self._watch_run.step_index = len(steps) - 1 if steps else -1
        self._update_watch_monitor()
        self._update_watch_dock()
        return True

    def _watch_monitor_signature(self):
        r = self._watch_run
        step = self._watch_selected_step()
        keys = tuple(self._watch_monitor_target_keys())
        cur_key = self._watch_ensure_monitor_target() if keys else ""
        step_sig = None
        if step is not None:
            step_sig = (
                step.id, step.title, step.phase, step.status, step.current,
                step.summary, step.digest, step.attention,
                tuple(step.recent_updates), tuple(step.evidence_lines),
                step.event_count, bool(step.ended_at),
            )
        # Keep elapsed-time polish without repainting the full monitor 3x/second.
        elapsed_bucket = int((time.monotonic() - r.started_at) // 2) if r.started_at else 0
        return (
            r.active, r.paused, r.pause_reason, r.phase, r.follow_latest,
            r.step_index, r.unseen_steps, r.pending_digest_reason,
            r.instruction_label, r.target_count, cur_key, keys, step_sig,
            r.last_micro_text, r.last_digest_text, r.last_alert_text,
            tuple(r.recent_updates), elapsed_bucket,
        )

    def _update_watch_monitor(self) -> None:
        if not self._watch_monitor_open:
            return
        sig = self._watch_monitor_signature()
        if sig == self._watch_monitor_render_sig:
            return
        self._watch_monitor_render_sig = sig
        try:
            self.query_one("#chat-pin", Static).update(self._watch_monitor_menu_text())
        except Exception:
            pass
        for wid, renderable in self._watch_monitor_sections().items():
            try:
                self.query_one(f"#{wid}", Static).update(renderable)
            except Exception:
                pass

    def _set_watch_monitor(self, open_: bool) -> None:
        self._watch_monitor_open = bool(open_)
        self._watch_monitor_render_sig = None
        try:
            self.query_one("#chat", VerticalScroll).display = not self._watch_monitor_open
        except Exception:
            pass
        monitor = self._watch_monitor()
        if monitor is not None:
            monitor.display = self._watch_monitor_open
            if self._watch_monitor_open:
                self._watch_run.follow_latest = True
                self._watch_run.unseen_steps = 0
                self._watch_ensure_monitor_target()
                steps = self._watch_filtered_steps()
                if steps:
                    self._watch_run.step_index = len(steps) - 1
                try:
                    monitor.scroll_home(animate=False)
                except Exception:
                    pass
        if self._watch_monitor_open:
            self._update_watch_monitor()
        else:
            self._update_chat_pin()
        self._update_watch_dock()
        self._focus_composer()

    def _close_watch_monitor_if_open(self) -> bool:
        if not self._watch_monitor_open:
            return False
        self._set_watch_monitor(False)
        return True

    def _update_watch_dock(self) -> None:
        dock = self._watch_dock()
        if dock is not None:
            dock.update(self._watch_dock_text())

    def _update_session_hud(self, snap=None) -> None:
        hud = self._session_hud()
        if hud is not None:
            hud.update(self._session_hud_text(snap))
        self._update_watch_dock()

    def _update_header(self):
        snap = _scope_snapshot(self.session)
        header = self._header()
        if header is not None:
            header.update(self._header_text(snap))
        self._update_session_hud(snap)

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
            ag = _agent_of(self.session)
            title = _short_activity(_session_title(self.session.st), 28)
            if title and title != "(untitled)":
                return f"{ag} session {title}"
            return f"{ag} session"
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
        self._update_watch_dock()
        self._update_watch_monitor()

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
        now = time.monotonic()
        scoped = self.session.scope != SC.SESSION
        scoped_due = scoped and (now - self._scoped_timeline_rebuild_at >= 5.0)
        if changed or scoped_due:
            self._rebuild_timeline()
        if self._watch_digest_due():
            self._watch_emit_digest()
        self._watch_maybe_heartbeat()
        if not scoped or changed or scoped_due:
            self._update_header()
        else:
            self._update_watch_dock()
        self._update_status()

    # ---- input ----
    @on(Composer.Submitted)
    def _on_submit(self, event: Composer.Submitted):
        self._slash_hide()
        self._mention_hide()
        text = event.text
        if text.startswith("@") and self._apply_scope_mention(text):
            return
        remembered = self._remember_prompt(text)
        if text.startswith("/"):
            low = text.strip().lower()
            if (self._watch_monitor_open and not (
                    low in ("/watch view", "/watch monitor")
                    or low.startswith("/watch refresh")
                    or low.startswith("/watch stop"))):
                self._set_watch_monitor(False)
            self._meta(text)
            return
        if self._watch_monitor_open:
            self._set_watch_monitor(False)
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
            self._msg_queue.append((text, self._evidence_sig(), self.session.store,
                                    remembered))
            self.notify(f"queued #{len(self._msg_queue)} — sends after the current "
                        "answer", severity="information")
            self._update_status()
            return
        self._begin_chat_turn(text, prompt_history_added=remembered)

    def _queue_item_live(self, item) -> bool:
        """True if a queued (text, sig, store) still belongs to the current
        evidence context — same scope/session and the same conversation."""
        _text, sig, store = item[:3]
        return sig == self._evidence_sig() and self._same_conv(store, self.session.store)

    def _prune_stale_queue(self) -> int:
        """Drop queued messages whose evidence context no longer matches. Returns
        how many were dropped (silent — the caller decides whether to announce)."""
        before = len(self._msg_queue)
        self._msg_queue[:] = [m for m in self._msg_queue if self._queue_item_live(m)]
        return before - len(self._msg_queue)

    def _begin_chat_turn(self, text: str, prompt_history_added: bool = False) -> bool:
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
        prompt = self._prompt_widget(text)
        self._chat(prompt)
        scope_marker = self._make_answer_scope_widget()
        self._chat(scope_marker)
        self.session.refresh()
        ctx = self.session.answer_context(text, history=list(self.session.history))
        self._ctx_stats = ctx.stats
        self._out_tokens = 0
        self._out_exact = False
        self._last_cost = None
        self._stream_md = None
        self._stream_buf = ""
        self._answer_abandoned = False
        self._answer_stop_reverted = False
        self._answer_store = self.session.store
        self._answer_prompt_widget = prompt
        self._answer_scope_marker_widget = scope_marker
        self._answer_prompt_text = text
        self._answer_prompt_history_added = bool(prompt_history_added)
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
        item = self._msg_queue.pop(0)
        text = item[0]
        remembered = bool(item[3]) if len(item) > 3 else False
        if (not self._begin_chat_turn(text, prompt_history_added=remembered)
                and self._msg_queue):
            # a guard blocked it (backend vanished) — drop the rest, don't spin.
            self.notify(f"dropped {len(self._msg_queue)} queued message(s)",
                        severity="warning")
            self._msg_queue.clear()

    def _rollback_queued_prompt_history(self) -> None:
        for item in reversed(self._msg_queue):
            text = item[0]
            remembered = bool(item[3]) if len(item) > 3 else False
            self._rollback_prompt_history(text, remembered)

    def _restore_stopped_prompt(self, text: str) -> None:
        if not str(text or "").strip():
            return
        try:
            comp = self.query_one("#composer", Composer)
        except Exception:
            return
        comp._replace_text(text)
        self._slash_hide()
        comp.focus()

    def _remove_stopped_turn_widgets(self, prompt_widget=None, answer_widget=None,
                                     scope_widget=None) -> None:
        widgets = []
        for widget in (prompt_widget, scope_widget, answer_widget):
            if widget is None:
                continue
            if not getattr(widget, "is_attached", False):
                continue
            if getattr(widget, "_pruning", False):
                continue
            widgets.append(widget)
        if not widgets:
            return
        try:
            self.query_one("#chat", VerticalScroll).remove_children(widgets)
        except Exception:
            for widget in widgets:
                try:
                    widget.remove()
                except Exception:
                    pass
        self._chat_prompt_nav_index = None
        self._update_chat_pin()

    def _rollback_stopped_turn_ui(self, text, prompt_widget=None,
                                  answer_widget=None,
                                  prompt_history_added=False,
                                  scope_widget=None) -> None:
        self._remove_stopped_turn_widgets(prompt_widget, answer_widget, scope_widget)
        self._rollback_prompt_history(text, bool(prompt_history_added))
        self._restore_stopped_prompt(text)

    def _revert_stopped_turn_ui(self, text=None, prompt_widget=None,
                                answer_widget=None, prompt_history_added=None) -> None:
        """Remove the in-flight turn from the chat and put its prompt back for edit."""
        if self._answer_stop_reverted:
            return
        self._answer_stop_reverted = True
        text = self._answer_prompt_text if text is None else text
        prompt_widget = (self._answer_prompt_widget if prompt_widget is None
                         else prompt_widget)
        scope_widget = self._answer_scope_marker_widget
        answer_widget = self._stream_md if answer_widget is None else answer_widget
        if prompt_history_added is None:
            prompt_history_added = self._answer_prompt_history_added
        self._rollback_stopped_turn_ui(text, prompt_widget, answer_widget,
                                       prompt_history_added, scope_widget)

    def action_stop_answer(self) -> None:
        """Ctrl+Z / `/stop`: interrupt the in-flight answer without quitting the
        app. A decisive stop — the pending queue is cleared so you can steer."""
        if not self._busy:
            self.notify("nothing to stop", severity="information")
            return
        cleared = len(self._msg_queue)
        self._rollback_queued_prompt_history()
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
        self._answer_stopped = True     # _answer_done consumes this; no save
        if self._same_conv(self._answer_store, self.session.store):
            self._revert_stopped_turn_ui()
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
        if self._answer_stopped:
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
        prompt_widget = self._answer_prompt_widget
        scope_widget = self._answer_scope_marker_widget
        prompt_text = self._answer_prompt_text or text
        prompt_history_added = self._answer_prompt_history_added
        stop_reverted = self._answer_stop_reverted
        self._stream_md = None
        self._stream_buf = ""
        self._answer_prompt_widget = None
        self._answer_scope_marker_widget = None
        self._answer_prompt_text = ""
        self._answer_prompt_history_added = False
        self._answer_stop_reverted = False
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
            # user-initiated stop (ctrl+z): roll the whole in-flight turn out of
            # chat history and put the prompt back in the composer. Persistence
            # was already skipped above; this is a UI rollback, not an error row.
            if not stop_reverted:
                self._rollback_stopped_turn_ui(prompt_text, prompt_widget, md,
                                               prompt_history_added, scope_widget)
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
    def _ensure_watch_worker(self):
        if self._watch_worker_started:
            return
        self._watch_worker_started = True
        self.watch_agent()

    @staticmethod
    def _watch_target_key(path: str) -> str:
        return os.path.abspath(path or "")

    def _watch_target_from_ref(self, ref=None, st=None):
        path = getattr(ref, "path", "") if ref is not None else self.session.path
        if not path:
            return None
        here = os.path.abspath(self.session.path or "")
        if st is None and os.path.abspath(path) == here:
            st = self.session.st
        if st is None:
            try:
                st = S.cached_build(path, SRC.parse)
            except Exception:
                st = None
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        agent = getattr(ref, "agent", "") if ref is not None else ""
        if not agent:
            try:
                agent = SRC.source_for_path(path).name
            except Exception:
                agent = _agent_of(self.session)
        sid = getattr(ref, "session_id", "") if ref is not None else ""
        sid = sid or _sid(st=st, path=path)
        title = (getattr(ref, "title", "") if ref is not None else "") or (
            _session_title(st, ref) if st is not None else "")
        return _WatchTarget(path=path, session_id=sid, agent=agent or "claude",
                            title=title, size=size, state=st)

    def _watch_scope_targets(self) -> list:
        if self.session.scope == SC.SESSION:
            target = self._watch_target_from_ref(st=self.session.st)
            return [target] if target is not None and target.state is not None else []
        try:
            refs = SC.resolve_session_refs(self.session.path, self.session.scope_sessions)
        except ValueError:
            return []
        targets = []
        here = os.path.abspath(self.session.path or "")
        for ref in refs:
            st = self.session.st if os.path.abspath(ref.path) == here else None
            target = self._watch_target_from_ref(ref, st=st)
            if target is not None and target.state is not None:
                targets.append(target)
        return targets

    def _reset_watch_baseline(self):
        self._watch_path = self.session.path
        self._watch_size = self.session.last_size
        self._watch_state = self.session.st
        self._watch_run.scope_sig = self._evidence_sig()
        targets = self._watch_scope_targets()
        self._watch_run.targets = {
            self._watch_target_key(t.path): t for t in targets
        }
        self._watch_run.target_count = len(targets)
        return targets

    def _watch_scope_changed(self, reason="evidence changed"):
        was_active = self._watch_mode and not self._watch_run.paused
        self._reset_watch_baseline()
        if not was_active:
            return
        self._watch_run.paused = True
        self._watch_run.pause_reason = reason
        self._watch_run.last_emit_at = time.monotonic()
        self._watch_run.last_alert_text = f"paused: {reason}; run /watch to resume"
        self._watch_begin_step("paused", trigger=reason,
                               current=self._watch_current_activity(),
                               attention=self._watch_run.last_alert_text,
                               force=True)
        self._watch_add_recent("paused", self._watch_run.last_alert_text)
        t = Text()
        t.append("watch · paused", style=_PAL["warning"])
        t.append(f" · {reason}; run /watch to resume on this scope",
                 style=_PAL["muted"])
        self._watch_chat(self._role(t, "role-event"))
        self._update_header()
        self._update_status()

    def _watch_target_subject(self, target=None, st=None, path=None) -> str:
        if target is not None:
            st = target.state if st is None else st
            path = target.path
            ag = target.agent or _agent_of(self.session)
            title = _short_activity(target.title or _session_title(st), 32) if st is not None else ""
            sid = target.session_id or _sid(st=st, path=path)
        else:
            st = self.session.st if st is None else st
            path = self.session.path if path is None else path
            ag = _agent_of(self.session)
            title = _short_activity(_session_title(st), 32) if st is not None else ""
            sid = _sid(st=st, path=path)
        if title and title != "(untitled)":
            return f"{ag} session {title} · {sid}"
        return f"{ag} session · {sid}"

    def _watch_target_display(self, target=None, st=None, path=None,
                              limit: int = 44) -> str:
        if target is not None:
            st = target.state if st is None else st
            ag = target.agent or _agent_of(self.session)
            title = _short_activity(target.title or _session_title(st), limit) \
                if st is not None else ""
        else:
            st = self.session.st if st is None else st
            ag = _agent_of(self.session)
            title = _short_activity(_session_title(st), limit) if st is not None else ""
        if title and title != "(untitled)":
            return title
        return f"{ag} session"

    def _watch_target_style(self, target=None) -> str:
        ag = (getattr(target, "agent", "") if target is not None else "") \
             or _agent_of(self.session)
        return _agent_hex(ag)

    def _watch_target_short(self, target=None, st=None, path=None) -> str:
        if target is not None:
            sid = (target.session_id or _sid(st=st or target.state, path=target.path))[:8]
            title = _short_activity(target.title or _session_title(st or target.state), 24)
            return f"{sid} {title}".strip() if title and title != "(untitled)" else sid
        sid = _sid(st=st or self.session.st, path=path or self.session.path)[:8]
        return sid or "session"

    def _watch_target_fields(self, target=None, st=None, path=None) -> tuple[str, str]:
        if target is None or self.session.scope == SC.SESSION:
            return "", ""
        key = self._watch_target_key(target.path)
        return key, self._watch_target_short(target, st=st, path=path)

    def _watch_monitor_target_keys(self) -> list:
        if self.session.scope == SC.SESSION:
            return []
        return [k for k, t in self._watch_run.targets.items()
                if t is not None and getattr(t, "state", None) is not None]

    def _watch_ensure_monitor_target(self) -> str:
        keys = self._watch_monitor_target_keys()
        if not keys:
            self._watch_run.monitor_target_key = ""
            return ""
        if self._watch_run.monitor_target_key not in keys:
            self._watch_run.monitor_target_key = keys[0]
        return self._watch_run.monitor_target_key

    def _watch_step_visible_in_monitor(self, step) -> bool:
        key = self._watch_ensure_monitor_target()
        if not key:
            return True
        return not getattr(step, "target_key", "") or step.target_key == key

    def _watch_filtered_steps(self) -> list:
        return [s for s in self._watch_run.steps if self._watch_step_visible_in_monitor(s)]

    def _watch_subject(self) -> str:
        if self.session.scope != SC.SESSION:
            count = self._watch_run.target_count or len(self._watch_run.targets)
            suffix = f" · {count} session{'s' if count != 1 else ''}" if count else ""
            return f"{self.session.scope_label()}{suffix}"
        return self._watch_target_subject()

    def _watch_current_activity(self, st=None, target=None) -> str:
        if target is not None and st is None:
            st = target.state
        elif st is None and self.session.scope != SC.SESSION:
            count = self._watch_run.target_count or len(self._watch_run.targets)
            return f"watching {count} session{'s' if count != 1 else ''}"
        st = self.session.st if st is None else st
        if st is None:
            return "history-only; transcript unavailable"
        pending = getattr(st, "pending_tool", None)
        if pending is not None:
            target = _short_activity(_tool_activity_target(pending), 90)
            return (pending.tool_name or "tool") + (" running: " + target if target else " running")
        if getattr(st, "commands", None):
            cmd = _short_activity(st.commands[-1].cmd, 90)
            return f"latest command: {cmd}" if cmd else st.status
        return f"{st.status} · idle {_dur(st.idle_seconds)}"

    def _watch_step_title(self, phase: str, trigger: str = "") -> str:
        phase = (phase or "watch").replace("-", " ")
        if trigger == "start":
            return "watch started"
        if trigger == "completion":
            return "completed"
        if trigger.startswith("phase "):
            return phase
        if trigger:
            return _short_activity(trigger.replace("-", " "), 48)
        return phase

    def _watch_latest_step(self):
        steps = self._watch_run.steps
        return steps[-1] if steps else None

    def _watch_latest_step_for(self, target_key: str = ""):
        if not target_key:
            return self._watch_latest_step()
        for step in reversed(self._watch_run.steps):
            if getattr(step, "target_key", "") == target_key:
                return step
        return None

    def _watch_selected_step(self):
        r = self._watch_run
        steps = self._watch_filtered_steps()
        if not steps:
            return None
        if r.follow_latest or r.step_index < 0 or r.step_index >= len(steps):
            r.step_index = len(steps) - 1
            return steps[-1]
        return steps[r.step_index]

    @staticmethod
    def _watch_step_is_generic(step) -> bool:
        if step is None:
            return True
        title = (getattr(step, "title", "") or "").strip().lower()
        phase = (getattr(step, "phase", "") or "").replace("-", " ").strip().lower()
        return (getattr(step, "trigger", "") == "start"
                or title == "watch started"
                or not title
                or (phase and title == phase)
                or phase in ("watching sessions", "watch", "unknown"))

    def _watch_soft_step_title(self, title: str, phase: str = "") -> str:
        title = _short_activity(title or "", 56)
        if title.startswith("phase "):
            return self._watch_step_title(phase or "watch")
        return title

    def _watch_merge_step_identity(self, step, title: str = "", phase: str = "",
                                   trigger: str = "") -> None:
        if step is None:
            return
        generic = self._watch_step_is_generic(step)
        if phase and (generic or not getattr(step, "phase", "")):
            step.phase = phase
        title = self._watch_soft_step_title(title, phase)
        if title and generic:
            step.title = title
            if trigger:
                step.trigger = trigger

    def _watch_new_step_allowed(self, fallback: dict, phase: str = "",
                                target_key: str = "") -> bool:
        latest = self._watch_latest_step_for(target_key)
        if latest is None:
            return True
        trigger = str(fallback.get("trigger", "") or "")
        if fallback.get("attention") or trigger == "completion" \
                or phase in ("complete", "needs-attention", "stalled"):
            return True
        if self._watch_step_is_generic(latest):
            return False
        if not phase or phase == getattr(latest, "phase", ""):
            return False
        elapsed = time.monotonic() - getattr(latest, "started_at", time.monotonic())
        events = int(getattr(latest, "event_count", 0) or 0) \
            + int(fallback.get("events", 0) or 0)
        return events >= _WATCH_STEP_MIN_EVENTS or elapsed >= _WATCH_STEP_MIN_SECONDS

    def _watch_begin_step(self, phase: str = "", trigger: str = "",
                          current: str = "", attention: str = "",
                          force: bool = False, target_key: str = "",
                          target_label: str = ""):
        r = self._watch_run
        phase = phase or r.phase or self._watch_phase(self.session.st)
        current = current or self._watch_current_activity()
        latest = self._watch_latest_step_for(target_key)
        if latest is not None and not force and latest.phase == phase:
            latest.current = current or latest.current
            if attention:
                latest.attention = attention
                latest.status = "attention"
            return latest
        now = time.monotonic()
        if latest is not None and not latest.ended_at:
            latest.ended_at = now
            if latest.status == "active":
                latest.status = "done"
        r.step_seq += 1
        step = _WatchStep(
            id=r.step_seq,
            title=self._watch_step_title(phase, trigger),
            phase=phase,
            trigger=trigger or phase,
            started_at=now,
            target_key=target_key,
            target_label=target_label,
            status="attention" if attention else "active",
            current=current,
            attention=attention,
        )
        r.steps.append(step)
        if len(r.steps) > _WATCH_STEP_LIMIT:
            overflow = len(r.steps) - _WATCH_STEP_LIMIT
            del r.steps[:overflow]
            if r.step_index >= 0:
                r.step_index = max(0, r.step_index - overflow)
        if target_key and r.follow_latest:
            r.monitor_target_key = target_key
        if r.follow_latest or r.step_index < 0:
            r.step_index = len(self._watch_filtered_steps()) - 1
            r.unseen_steps = 0
        else:
            r.unseen_steps += 1
        return step

    def _watch_update_step(self, *, current: str = "", summary: str = "",
                           digest: str = "", attention: str = "",
                           recent: str = "", evidence: str = "",
                           events: int = 0, target_key: str = "",
                           target_label: str = "") -> None:
        if target_key and self._watch_run.follow_latest:
            self._watch_run.monitor_target_key = target_key
        step = self._watch_latest_step_for(target_key) or self._watch_begin_step(
            target_key=target_key, target_label=target_label)
        if current:
            step.current = current
        if summary:
            step.summary = summary
        if digest:
            step.digest = digest
        if attention:
            step.attention = attention
            step.status = "attention"
        if recent:
            step.recent_updates.append(recent)
            if len(step.recent_updates) > _WATCH_RECENT_LIMIT:
                del step.recent_updates[:len(step.recent_updates) - _WATCH_RECENT_LIMIT]
        if evidence:
            step.evidence_lines.append(evidence)
            if len(step.evidence_lines) > _WATCH_RECENT_LIMIT:
                del step.evidence_lines[:len(step.evidence_lines) - _WATCH_RECENT_LIMIT]
        if events:
            step.event_count += max(0, int(events))
        if target_key and self._watch_run.follow_latest:
            steps = self._watch_filtered_steps()
            self._watch_run.step_index = len(steps) - 1 if steps else -1

    def _watch_finish_current_step(self, status: str = "done") -> None:
        step = self._watch_latest_step()
        if step is None:
            return
        step.ended_at = step.ended_at or time.monotonic()
        step.status = status

    def _watch_start_text(self, reset=False) -> Text:
        t = Text()
        t.append("watch · ", style=_PAL["muted"])
        if reset:
            t.append("baseline reset", style=_PAL["accent"])
        else:
            t.append("Night gathers, and now my watch begins.", style=_PAL["accent"])
        t.append(f" · {self._watch_subject()}", style=_PAL["muted"])
        if self._watch_run.instruction_label:
            t.append(f" · {self._watch_run.instruction_label}", style=_PAL["secondary"])
        t.append(f" · {self._watch_current_activity()}", style=_PAL["text"])
        return t

    def _watch_stop_text(self) -> Text:
        step = self._watch_latest_step()
        count = len(self._watch_run.steps)
        t = Text()
        t.append("watch · stopped", style=_PAL["muted"])
        if count:
            suffix = "step" if count == 1 else "steps"
            t.append(f" · {count} {suffix}", style=_PAL["secondary"])
        if step is not None and step.title:
            t.append(f" · latest: {_short_activity(step.title, 48)}", style=_PAL["text"])
        if count:
            t.append(" · /watch view to review", style=_PAL["accent"])
        return t

    def _watch_status_body(self) -> str:
        r = self._watch_run
        state = "paused" if r.active and r.paused else ("on" if r.active else "off")
        since = ""
        now = time.monotonic()
        if r.active and r.started_at:
            since = f" · for {_dur(time.monotonic() - self._watch_started_at)}"
        last = f"last update: {_dur(now - r.last_emit_at)} ago" if r.last_emit_at else "last update: none"
        if r.active and not r.paused and r.last_digest_at:
            remaining = max(0, int(r.digest_interval - (now - r.last_digest_at)))
            digest = f"next digest: about {_dur(remaining)}"
        elif r.paused:
            digest = f"next digest: paused ({r.pause_reason or 'scope paused'})"
        else:
            digest = "next digest: off"
        lines = [
            f"watch: {state}{since}",
            f"target: {self._watch_subject()}",
            f"current: {self._watch_current_activity()}",
            f"cadence: {r.mode} · micro >= {_dur(r.micro_interval)} · digest {_dur(r.digest_interval)} or {r.digest_events} events",
            last,
            digest,
            "mode: read-only progress watcher; no prompts are injected into the agent",
        ]
        if r.instruction_label:
            lines.insert(3, f"steer: {r.instruction_label}")
        return "\n".join(lines)

    def _watch_delta_text(self, st, d, target=None) -> str:
        subject = self._watch_target_subject(target, st=st)
        rows = [
            "# cc-copilot watch delta",
            f"- watched scope: `{self._watch_subject()}`",
            f"- changed session: `{subject}`",
            f"- new transcript events since last watch update: {d.new_events}",
            f"- status: `{d.status_from or 'empty'}` -> `{d.status_to}`",
            f"- safety: `{d.verdict_from or 'empty'}` -> `{d.verdict_to}`",
            f"- current activity: {self._watch_current_activity(st, target=target)}",
        ]
        if self._watch_run.instruction:
            rows.append(f"- watch instruction: {self._watch_run.instruction}")
        pending = getattr(st, "pending_tool", None)
        if pending is not None:
            rows.append(f"- in-flight tool: `{pending.tool_name or '?'}` [L{pending.line}]")
        last = getattr(st, "last_record", None)
        if last is not None and getattr(last, "line", 0):
            text = _short_activity(getattr(last, "text", "") or getattr(last, "kind", ""), 180)
            rows.append(f"- latest observed event: {text} [L{last.line}]")
        for fc in d.new_changed[:5]:
            rows.append(f"- changed file: `{fc.path}` ({fc.total} edit/write) [L{fc.last_line}]")
        for f in d.new_failures[:3]:
            target = f" on `{f.target}`" if getattr(f, "target", "") else ""
            rows.append(f"- failure: `{f.tool}`{target} [L{f.line}]: {_short_activity(f.summary, 180)}")
        return "\n".join(rows)

    def _watch_step_decision_text(self, delta_text: str) -> str:
        step = self._watch_latest_step()
        rows = ["# cc-copilot watch step decision"]
        if step is None:
            rows.append("- current step: none")
        else:
            rows.extend([
                "## current watch step",
                f"- title: {step.title}",
                f"- phase: {step.phase}",
                f"- status: {step.status}",
                f"- current: {step.current or 'none'}",
                f"- summary: {step.summary or 'none'}",
                f"- digest: {step.digest or 'none'}",
                f"- attention: {step.attention or 'none'}",
            ])
            if step.evidence_lines:
                rows.append("- recent evidence: " + " | ".join(step.evidence_lines[-3:]))
        rows.extend(["", "## new watch delta", delta_text])
        return "\n".join(rows)

    def _watch_apply_step_decision(self, decision_text: str, fallback=None) -> None:
        fallback = fallback or {}
        decision = _parse_watch_step_decision(decision_text)
        if not decision:
            decision = {
                "action": "new" if fallback.get("new_step") else "same",
                "title": fallback.get("title", ""),
                "phase": fallback.get("phase", ""),
                "reason": fallback.get("trigger", ""),
                "attention": fallback.get("attention", "") or "none",
            }
        phase = (decision.get("phase") or fallback.get("phase") or self._watch_run.phase
                 or self._watch_phase(self.session.st))
        title = _short_activity(decision.get("title", ""), 56)
        reason = decision.get("reason") or fallback.get("trigger", "")
        attention = decision.get("attention", "")
        if attention.lower() == "none":
            attention = ""
        target_key = fallback.get("target_key", "")
        target_label = fallback.get("target_label", "")
        action = decision.get("action")
        if action == "new" and not self._watch_new_step_allowed(fallback, phase, target_key):
            action = "same"
        if action == "new":
            step = self._watch_begin_step(
                phase,
                trigger=("semantic: " + reason) if reason else "semantic step",
                current=fallback.get("current", ""),
                attention=attention or fallback.get("attention", ""),
                force=True,
                target_key=target_key,
                target_label=target_label,
            )
            if title:
                step.title = title
            self._watch_run.phase = phase
        elif title:
            step = self._watch_latest_step_for(target_key)
            self._watch_merge_step_identity(
                step, title=title, phase=phase,
                trigger=("semantic: " + reason) if reason else "semantic same")
        self._watch_update_step(current=fallback.get("current", ""),
                                attention=attention or fallback.get("attention", ""),
                                evidence=fallback.get("evidence", ""),
                                events=fallback.get("events", 0),
                                target_key=target_key,
                                target_label=target_label)

    def _watch_high_risk(self, d) -> bool:
        return bool(d.new_failures or d.status_to == "stalled"
                    or d.verdict_to == "intervene")

    def _watch_digest_evidence_line(self, st, d, target=None) -> str:
        bits = []
        if d.new_events:
            bits.append(f"+{d.new_events} transcript events")
        if d.status_from != d.status_to:
            bits.append(f"status `{d.status_from or 'empty'}` -> `{d.status_to}`")
        if d.verdict_from != d.verdict_to:
            bits.append(f"safety `{d.verdict_from or 'empty'}` -> `{d.verdict_to}`")
        pending = getattr(st, "pending_tool", None)
        if pending is not None:
            bits.append(f"in-flight `{pending.tool_name or '?'}` [L{pending.line}]")
        if d.new_changed:
            files = ", ".join(fc.path for fc in d.new_changed[:4])
            extra = "" if len(d.new_changed) <= 4 else f", +{len(d.new_changed) - 4} more"
            bits.append(f"changed {files}{extra} [L{d.new_changed[-1].last_line}]")
        if d.new_failures:
            f = d.new_failures[-1]
            bits.append(f"{f.tool} failed [L{f.line}]: {_short_activity(f.summary, 140)}")
        if not bits:
            bits.append(self._watch_current_activity(st, target=target))
        phase = self._watch_phase(st, d)
        self._watch_run.phase = phase
        if target is not None or self.session.scope != SC.SESSION:
            return f"- `{self._watch_target_short(target, st=st)}` phase `{phase}`: " + "; ".join(bits)
        return f"- phase `{phase}`: " + "; ".join(bits)

    def _watch_phase(self, st, d=None) -> str:
        if d is not None and self._watch_high_risk(d):
            return "needs-attention"
        pending = getattr(st, "pending_tool", None)
        if pending is not None:
            name = (pending.tool_name or "tool").strip().lower()
            if name == "bash":
                target = _tool_activity_target(pending).lower()
                if any(x in target for x in ("pytest", " test", "npm test", "pnpm test",
                                             "vitest", "cargo test", "go test")):
                    return "testing"
                if any(x in target for x in (" build", "npm run build", "pnpm build",
                                             "cargo build", "make ")):
                    return "building"
                return "running-command"
            if name in ("edit", "write", "multiedit"):
                return "editing"
            return f"running-{name}"
        if getattr(st, "status", "") == "stalled":
            return "stalled"
        if getattr(st, "status", "") == "running":
            return "running"
        if getattr(st, "commands", None):
            return "command-finished"
        return getattr(st, "status", "") or "unknown"

    def _watch_buffer_line(self, line: str) -> None:
        line = " ".join(str(line or "").split())
        if not line:
            return
        buf = self._watch_run.digest_buffer
        if buf and buf[-1] == line:
            return
        buf.append(line)
        if len(buf) > _WATCH_BUFFER_LIMIT:
            del buf[:len(buf) - _WATCH_BUFFER_LIMIT]

    def _watch_add_recent(self, kind: str, body: str,
                          target_key: str = "", target_label: str = "",
                          attach_step: bool = True) -> None:
        body = " ".join(str(body or "").split())
        if not body:
            return
        line = f"{time.strftime('%H:%M')} {kind} · {body}"
        recent = self._watch_run.recent_updates
        if recent and recent[-1] == line:
            return
        recent.append(line)
        if len(recent) > _WATCH_RECENT_LIMIT:
            del recent[:len(recent) - _WATCH_RECENT_LIMIT]
        if attach_step and self._watch_run.steps:
            self._watch_update_step(recent=line, target_key=target_key,
                                    target_label=target_label)

    def _watch_buffer_delta(self, st, d, semantic: bool = False, target=None) -> dict:
        if not self._watch_mode or self._watch_run.paused:
            return {}
        old_phase = self._watch_run.phase
        evidence = self._watch_digest_evidence_line(st, d, target=target)
        self._watch_buffer_line(evidence)
        new_phase = self._watch_run.phase
        current = self._watch_current_activity(st, target=target)
        subject_short = self._watch_target_short(target, st=st)
        scoped_target = target is not None or self.session.scope != SC.SESSION
        target_key, target_label = self._watch_target_fields(target, st=st)
        completion = d.status_to == "idle" and d.status_from and d.status_from != "idle"
        high_risk = self._watch_high_risk(d)
        trigger = ""
        if completion:
            trigger = "completion"
        elif old_phase and old_phase != "idle" and new_phase and new_phase != old_phase:
            trigger = f"phase {old_phase} -> {new_phase}"
        elif new_phase and new_phase != old_phase:
            trigger = f"phase {old_phase or 'start'} -> {new_phase}"
        elif high_risk:
            trigger = "attention"
        fallback = {
            "new_step": bool(trigger),
            "phase": "complete" if completion else new_phase,
            "title": self._watch_step_title("complete" if completion else new_phase, trigger),
            "trigger": trigger,
            "current": f"{subject_short} · {current}" if scoped_target else current,
            "attention": current if high_risk else "",
            "evidence": evidence,
            "events": max(0, int(d.new_events or 0)),
            "subject": subject_short,
            "target_key": target_key,
            "target_label": target_label,
        }
        if scoped_target and fallback["title"]:
            fallback["title"] = f"{subject_short}: {fallback['title']}"
        if semantic:
            self._watch_run.last_phase = old_phase
            self._watch_run.events_since_digest += max(0, int(d.new_events or 0))
            if old_phase and old_phase != "idle" and new_phase and new_phase != old_phase:
                self._watch_run.phase_digest_pending = True
                self._watch_run.pending_digest_reason = f"phase {old_phase} -> {new_phase}"
            if completion:
                self._watch_run.done_digest_pending = True
                self._watch_run.pending_digest_reason = "completion"
            return fallback
        allow_new = self._watch_new_step_allowed(fallback, fallback["phase"], target_key)
        if fallback["new_step"] and allow_new:
            self._watch_begin_step(
                fallback["phase"],
                trigger=trigger or (f"phase {old_phase or 'start'} -> {new_phase}"
                                    if old_phase else "phase start"),
                current=fallback["current"],
                attention=(current if high_risk else ""),
                force=True,
                target_key=target_key,
                target_label=target_label,
            )
            if scoped_target:
                step = self._watch_latest_step_for(target_key)
                if step is not None:
                    step.title = fallback["title"]
        elif fallback["new_step"]:
            self._watch_merge_step_identity(
                self._watch_latest_step_for(target_key),
                title=fallback.get("title", ""),
                phase=fallback.get("phase", ""),
                trigger=fallback.get("trigger", ""))
        elif not self._watch_run.steps:
            self._watch_begin_step(new_phase, trigger="activity",
                                   current=fallback["current"],
                                   target_key=target_key,
                                   target_label=target_label)
        self._watch_update_step(current=fallback["current"],
                                evidence=evidence, events=d.new_events or 0,
                                target_key=target_key,
                                target_label=target_label)
        if old_phase and old_phase != "idle" and new_phase and new_phase != old_phase:
            self._watch_run.phase_digest_pending = True
            self._watch_run.pending_digest_reason = f"phase {old_phase} -> {new_phase}"
        self._watch_run.last_phase = old_phase
        self._watch_run.events_since_digest += max(0, int(d.new_events or 0))
        if completion and self._watch_new_step_allowed(
                {**fallback, "trigger": "completion"}, "complete", target_key):
            self._watch_begin_step("complete", trigger="completion",
                                   current=fallback["current"],
                                   force=True,
                                   target_key=target_key,
                                   target_label=target_label)
            if scoped_target:
                step = self._watch_latest_step_for(target_key)
                if step is not None:
                    step.title = fallback["title"]
            self._watch_update_step(current=fallback["current"],
                                    evidence=evidence, events=d.new_events or 0,
                                    target_key=target_key,
                                    target_label=target_label)
            self._watch_run.done_digest_pending = True
            self._watch_run.pending_digest_reason = "completion"
        return fallback

    def _watch_should_emit_micro(self, d, now=None) -> bool:
        if not self._watch_mode or self._watch_run.paused:
            return False
        now = time.monotonic() if now is None else now
        if self._watch_high_risk(d):
            self._watch_run.last_alert_at = now
            return True
        if self._watch_run.mode == "quiet":
            return False
        return (not self._watch_run.last_micro_at
                or now - self._watch_run.last_micro_at >= self._watch_run.micro_interval)

    def _watch_pending_target(self):
        if self.session.scope != SC.SESSION:
            for target in self._watch_run.targets.values():
                if getattr(target.state, "pending_tool", None) is not None:
                    return target
        return None

    def _watch_maybe_heartbeat(self, now=None) -> bool:
        r = self._watch_run
        if not r.active or r.paused or self._busy:
            return False
        target = self._watch_pending_target()
        st = target.state if target is not None else self.session.st
        pending = getattr(st, "pending_tool", None) if st is not None else None
        if pending is None:
            return False
        now = time.monotonic() if now is None else now
        base = max(r.last_emit_at or 0, r.started_at or 0, r.last_heartbeat_at or 0)
        if not base or now - base < r.heartbeat_interval:
            return False
        activity = self._watch_current_activity(st, target=target)
        if target is not None:
            activity = f"{self._watch_target_short(target, st=st)} · {activity}"
        body = f"still running: {activity}"
        target_key, target_label = self._watch_target_fields(target, st=st)
        r.last_heartbeat_at = now
        r.last_emit_at = now
        r.last_micro_text = body
        self._watch_update_step(current=body, summary=body,
                                target_key=target_key, target_label=target_label)
        self._watch_add_recent("heartbeat", body, target_key=target_key,
                               target_label=target_label)
        t = Text()
        t.append("watch · heartbeat", style=_PAL["accent"])
        t.append(" · ", style=_PAL["muted"])
        t.append(body, style=_PAL["text"])
        self._watch_chat(self._role(t, "role-event"))
        return True

    def _watch_digest_reason(self, now=None) -> str:
        r = self._watch_run
        if not r.active or r.paused or not r.digest_buffer:
            return ""
        now = time.monotonic() if now is None else now
        if r.pending_digest_reason:
            return r.pending_digest_reason
        if r.done_digest_pending:
            return "completion"
        if r.phase_digest_pending:
            return "phase change"
        if r.events_since_digest >= r.digest_events:
            return f"{r.events_since_digest} events"
        if r.last_digest_at and now - r.last_digest_at >= r.digest_interval:
            return f"{_dur(now - r.last_digest_at)} cadence"
        return ""

    def _watch_digest_due(self, now=None) -> bool:
        return bool(self._watch_digest_reason(now))

    def _watch_digest_text(self, reason: str = "") -> str:
        r = self._watch_run
        elapsed = _dur(time.monotonic() - r.started_at) if r.started_at else "0s"
        phase = r.phase or (
            "watching-sessions" if self.session.scope != SC.SESSION
            else self._watch_phase(self.session.st)
        )
        rows = [
            "# cc-copilot watch digest buffer",
            f"- watched scope: `{self._watch_subject()}`",
            f"- elapsed watch time: {elapsed}",
            f"- current phase: `{phase}`",
            f"- current activity: {self._watch_current_activity()}",
            f"- digest trigger: {reason or self._watch_digest_reason() or 'refresh'}",
            f"- buffered events since last digest: {r.events_since_digest}",
        ]
        if r.instruction:
            rows.append(f"- watch instruction: {r.instruction}")
        rows.extend(["", "## buffered watch evidence"])
        rows.extend(r.digest_buffer)
        return "\n".join(rows)

    def _watch_fallback_digest_body(self) -> str:
        items = self._watch_run.digest_buffer[-4:]
        if not items:
            return "No accumulated watch changes yet."
        compact = "; ".join(_short_activity(x.lstrip("- "), 160) for x in items)
        return "Recent watch evidence: " + compact

    def _watch_fallback_renderable(self, st, d, target=None):
        if not self._watch_mode or self._watch_run.paused:
            return None, "role-event"
        high_risk = self._watch_high_risk(d)
        meaningful = (high_risk or d.status_from != d.status_to
                      or d.verdict_from != d.verdict_to
                      or bool(d.new_changed) or bool(getattr(st, "pending_tool", None)))
        if not meaningful:
            return None, "role-event"

        t = Text()
        t.append("watch · ", style=_PAL["muted"])
        if high_risk:
            t.append("needs attention", style=_PAL["error"])
            cls = "role-alert"
        else:
            t.append("progress", style=_PAL["accent"])
            cls = "role-event"
        if d.new_events:
            t.append(f" · +{d.new_events} ev", style=_PAL["muted"])
        if target is not None or self.session.scope != SC.SESSION:
            t.append(f" · {self._watch_target_short(target, st=st)}",
                     style=_PAL["secondary"])
        if d.status_from != d.status_to:
            t.append(f" · {d.status_from or 'empty'} -> {d.status_to}",
                     style=_PAL["secondary"])
        if d.verdict_from != d.verdict_to:
            t.append(f" · safety {d.verdict_from or 'empty'} -> {d.verdict_to}",
                     style=_VERDICT_HEX.get(d.verdict_to, _PAL["muted"]))
        pending = getattr(st, "pending_tool", None)
        if pending is not None:
            t.append(f" · {self._watch_current_activity(st, target=target)}",
                     style=_PAL["text"])
        if d.new_changed:
            files = ", ".join(fc.path for fc in d.new_changed[:3])
            extra = "" if len(d.new_changed) <= 3 else f", +{len(d.new_changed) - 3} more"
            t.append(f" · changed: {files}{extra}", style=_PAL["success"])
        if d.new_failures:
            f = d.new_failures[-1]
            t.append(f" · {f.tool} failed [L{f.line}]", style=_PAL["error"])
            if f.summary:
                t.append(": " + _short_activity(f.summary, 90), style=_PAL["text"])

        plain = " ".join(t.plain.split())
        if plain == self._watch_last_summary:
            return None, cls
        self._watch_last_summary = plain
        now = time.monotonic()
        self._watch_run.last_micro_at = now
        self._watch_last_emit_at = now
        self._watch_run.last_micro_text = plain
        target_key, target_label = self._watch_target_fields(target, st=st)
        if high_risk:
            self._watch_run.last_alert_text = plain
            self._watch_run.last_alert_at = now
            self._watch_update_step(current=plain, summary=plain, attention=plain,
                                    target_key=target_key, target_label=target_label)
            self._watch_add_recent("alert", plain, target_key=target_key,
                                   target_label=target_label)
        else:
            self._watch_update_step(current=plain, summary=plain,
                                    target_key=target_key, target_label=target_label)
            self._watch_add_recent("micro", plain, target_key=target_key,
                                   target_label=target_label)
        return t, cls

    def _watch_progress_changed(self, st, d) -> bool:
        if not self._watch_mode or self._watch_run.paused:
            return False
        return (bool(d.new_failures) or d.status_from != d.status_to
                or d.verdict_from != d.verdict_to or bool(d.new_changed)
                or bool(getattr(st, "pending_tool", None)))

    def _watch_render_narration(self, text: str, cls="role-event",
                                target_key: str = "", target_label: str = "") -> None:
        body = " ".join(str(text or "").split())
        if not body:
            return
        plain = "watch copilot " + body
        if plain == self._watch_last_summary:
            return
        self._watch_last_summary = plain
        now = time.monotonic()
        self._watch_run.last_micro_at = now
        self._watch_last_emit_at = now
        self._watch_run.last_micro_text = body
        if cls == "role-alert":
            self._watch_run.last_alert_text = body
            self._watch_run.last_alert_at = now
            self._watch_update_step(current=body, summary=body, attention=body,
                                    target_key=target_key, target_label=target_label)
            self._watch_add_recent("alert", body, target_key=target_key,
                                   target_label=target_label)
        else:
            self._watch_update_step(current=body, summary=body,
                                    target_key=target_key, target_label=target_label)
            self._watch_add_recent("micro", body, target_key=target_key,
                                   target_label=target_label)
        self._watch_buffer_line(f"- micro summary: {body}")
        t = Text()
        t.append("watch · copilot", style=_PAL["accent"])
        t.append(" · ", style=_PAL["muted"])
        t.append(body, style=_PAL["text"])
        self._watch_chat(self._role(t, cls))

    def _watch_render_digest(self, text: str, cls="role-event", reason="") -> None:
        body = " ".join(str(text or "").split())
        if not body:
            return
        plain = "watch digest " + body
        if plain == self._watch_last_summary:
            return
        now = time.monotonic()
        self._watch_last_summary = plain
        self._watch_last_emit_at = now
        self._watch_run.last_digest_at = now
        self._watch_run.last_digest_text = body
        self._watch_run.last_digest_reason = reason or self._watch_digest_reason() or "refresh"
        self._watch_run.events_since_digest = 0
        self._watch_run.digest_buffer.clear()
        self._watch_run.pending_digest_reason = ""
        self._watch_run.phase_digest_pending = False
        self._watch_run.done_digest_pending = False
        if self.session.scope == SC.SESSION:
            self._watch_update_step(digest=body)
            self._watch_add_recent("digest", body)
        else:
            self._watch_add_recent("digest", body, attach_step=False)
        t = Text()
        t.append("watch · digest", style=_PAL["accent"])
        t.append(" · ", style=_PAL["muted"])
        t.append(body, style=_PAL["text"])
        self._watch_chat(self._role(t, cls))

    @work(thread=True)
    def _watch_narrate(self, delta_text, step_text, origin, cls="role-event",
                       instruction="", fallback=None):
        decision = ""
        try:
            decision = N.watch_step_decision(step_text, model=self.model,
                                             backend=self.backend,
                                             instruction=instruction)
        except Exception:
            decision = ""
        try:
            out = N.watch_progress_brief(delta_text, model=self.model,
                                         backend=self.backend,
                                         instruction=instruction)
        except Exception as e:
            out = f"(copilot watch summary unavailable: {e})"
        self.call_from_thread(self._watch_narration_done, out, origin, cls,
                              decision, fallback or {})

    def _watch_narration_done(self, out, origin, cls, decision="", fallback=None):
        self._watch_narrating = False
        sig, store = origin
        if not self._watch_mode:
            return
        if self._evidence_sig() != sig or self.session.store is not store:
            return
        fallback = fallback or {}
        self._watch_apply_step_decision(decision, fallback)
        self._watch_render_narration(out, cls,
                                     target_key=fallback.get("target_key", ""),
                                     target_label=fallback.get("target_label", ""))
        if self._watch_digest_due():
            self._watch_emit_digest()
        self._update_header()
        self._update_status()

    @work(thread=True)
    def _watch_digest_narrate(self, digest_text, origin, cls="role-event", reason="",
                              instruction=""):
        try:
            out = N.watch_digest_brief(digest_text, model=self.model,
                                       backend=self.backend,
                                       instruction=instruction)
        except Exception as e:
            out = f"(copilot watch digest unavailable: {e})"
        self.call_from_thread(self._watch_digest_done, out, origin, cls, reason)

    def _watch_digest_done(self, out, origin, cls, reason=""):
        self._watch_narrating = False
        sig, store = origin
        if not self._watch_mode or self._watch_run.paused:
            return
        if self._evidence_sig() != sig or self.session.store is not store:
            return
        self._watch_render_digest(out, cls, reason=reason)
        self._update_header()
        self._update_status()

    def _watch_emit_digest(self, manual=False, reason="") -> bool:
        r = self._watch_run
        reason = reason or self._watch_digest_reason() or ("refresh" if manual else "")
        if not r.active:
            if manual:
                self.notify("watch is off", severity="information")
            return False
        if r.paused:
            if manual:
                self.notify("watch is paused; run /watch to resume", severity="warning")
            return False
        if not r.digest_buffer:
            if manual:
                self._watch_render_digest("No accumulated watch changes yet.", reason=reason)
            return False
        if self._watch_narrating or self._busy:
            if reason:
                r.pending_digest_reason = reason
            if manual:
                self.notify("watch refresh queued", severity="information")
            return False
        if N.available(self.backend) and not self._busy:
            self._watch_narrating = True
            self._watch_digest_narrate(self._watch_digest_text(reason),
                                       (self._evidence_sig(), self.session.store),
                                       reason=reason, instruction=r.instruction)
        else:
            self._watch_render_digest(self._watch_fallback_digest_body(), reason=reason)
        return True

    def action_watch(self, arg=""):
        raw_arg = (arg or "").strip()
        low = raw_arg.lower()
        if low in ("status", "state"):
            self._result(self._watch_status_body(), markdown=False, title="/watch status")
            return
        if low in ("view", "monitor", "screen", "tv"):
            self._set_watch_monitor(True)
            return
        if low in ("refresh", "now", "digest", "summary", "summarize"):
            self._watch_emit_digest(manual=True, reason="refresh")
            self._update_header()
            self._update_status()
            return
        if low in ("stop", "off", "end"):
            if not self._watch_mode:
                self.notify("watch is already off", severity="information")
                self._update_header()
                return
            self._watch_mode = False
            self._watch_run.digest_buffer.clear()
            self._watch_run.events_since_digest = 0
            self._watch_run.pending_digest_reason = ""
            self._watch_run.phase_digest_pending = False
            self._watch_run.done_digest_pending = False
            self._watch_run.instruction = ""
            self._watch_run.instruction_label = ""
            self._watch_finish_current_step("stopped")
            stop_text = self._watch_stop_text()
            self._clear_watch_chat_ephemeral()
            self._chat(self._role(stop_text, "role-event"))
            if not self.alerts and self._watch_worker_started:
                old_stop = self._watch_stop
                self._watch_stop = threading.Event()
                self._watch_worker_started = False
                old_stop.set()
            self._update_header()
            self._update_status()
            return
        instruction_arg = raw_arg
        reset = self._watch_mode
        first, _space, rest = raw_arg.partition(" ")
        if low in ("", "start", "on", "reset"):
            instruction_arg = ""
            reset = self._watch_mode or low == "reset"
        elif first.lower() in ("start", "on", "reset"):
            instruction_arg = rest.strip()
            reset = self._watch_mode or first.lower() == "reset"
        instruction, instruction_label = _watch_instruction(instruction_arg)

        self.session.refresh()
        if self.session.st is None:
            self.notify("history-only view — /sessions to attach a live session",
                        severity="warning")
            return
        self._watch_mode = True
        now = time.monotonic()
        if not self._watch_started_at or not reset:
            self._watch_started_at = now
        self._watch_last_emit_at = now
        self._watch_run.paused = False
        self._watch_run.pause_reason = ""
        self._watch_run.last_micro_at = 0.0
        self._watch_run.last_digest_at = now
        self._watch_run.last_alert_at = 0.0
        self._watch_run.last_heartbeat_at = 0.0
        self._watch_last_summary = ""
        self._watch_run.last_micro_text = ""
        self._watch_run.last_digest_text = ""
        self._watch_run.last_alert_text = ""
        self._watch_run.last_digest_reason = ""
        self._watch_run.last_phase = ""
        self._watch_run.pending_digest_reason = ""
        self._watch_run.phase_digest_pending = False
        self._watch_run.done_digest_pending = False
        self._watch_run.instruction = instruction
        self._watch_run.instruction_label = instruction_label
        self._watch_run.digest_buffer.clear()
        self._watch_run.recent_updates.clear()
        self._watch_run.steps.clear()
        self._watch_run.step_seq = 0
        self._watch_run.step_index = -1
        self._watch_run.follow_latest = True
        self._watch_run.unseen_steps = 0
        self._watch_run.monitor_target_key = ""
        self._watch_run.events_since_digest = 0
        targets = self._reset_watch_baseline()
        if not targets:
            self._watch_mode = False
            self.notify("no live evidence sessions to watch", severity="warning")
            self._update_header()
            self._update_status()
            return
        self._watch_run.phase = (
            "watching-sessions" if self.session.scope != SC.SESSION
            else self._watch_phase(self.session.st)
        )
        self._watch_begin_step(self._watch_run.phase, trigger="start",
                               current=self._watch_current_activity(), force=True)
        self._watch_add_recent("start", f"{self._watch_subject()} · {self._watch_current_activity()}")
        self._ensure_watch_worker()
        self._clear_watch_chat_ephemeral()
        self._watch_chat(self._role(self._watch_start_text(reset=reset), "role-event"))
        self._update_header()
        self._update_status()

    @work(thread=True, exclusive=True, group="watch")
    def watch_agent(self):
        self._reset_watch_baseline()
        while not self._watch_stop.wait(self.poll):
            if not self._watch_mode or self._watch_run.paused:
                continue
            if self._evidence_sig() != self._watch_run.scope_sig:
                self.call_from_thread(self._watch_scope_changed, "evidence changed")
                continue
            targets = list(self._watch_run.targets.values())
            if not targets:
                targets = self._reset_watch_baseline()
            for target in targets:
                try:
                    size = os.path.getsize(target.path)
                except OSError:
                    continue
                if size == target.size:
                    continue
                target.size = size
                try:
                    st = S.cached_build(target.path, SRC.parse)
                except Exception:
                    continue
                d = S.diff(target.state, st)
                target.state = st
                if os.path.abspath(target.path) == os.path.abspath(self.session.path):
                    self._watch_size = size
                    self._watch_state = st
                self.call_from_thread(self._on_watch, st, d, target)

    def _on_watch(self, st, d, target=None):
        if target is None:
            target = self._watch_target_from_ref(st=st)
        if target is not None:
            target.state = st
            self._watch_run.targets[self._watch_target_key(target.path)] = target
            self._watch_run.target_count = len(self._watch_run.targets)
            self._watch_run.last_target_label = self._watch_target_short(target, st=st)
        if target is None or os.path.abspath(target.path) == os.path.abspath(self.session.path):
            self.session.st = st
            try:
                self.session.last_size = os.path.getsize(self.session.path)
            except OSError:
                pass
        self._update_header()
        self._update_status()
        if self._watch_progress_changed(st, d):
            cls = ("role-alert" if (d.new_failures or d.status_to == "stalled"
                                    or d.verdict_to == "intervene")
                   else "role-event")
            emit_micro = self._watch_should_emit_micro(d)
            use_semantic = (emit_micro and N.available(self.backend) and not self._busy
                            and not self._watch_narrating)
            fallback = self._watch_buffer_delta(st, d, semantic=use_semantic,
                                                target=target)
            if emit_micro:
                if (N.available(self.backend) and not self._busy
                        and not self._watch_narrating):
                    self._watch_narrating = True
                    self._watch_run.last_micro_at = time.monotonic()
                    delta_text = self._watch_delta_text(st, d, target=target)
                    self._watch_narrate(delta_text, self._watch_step_decision_text(delta_text),
                                        (self._evidence_sig(), self.session.store), cls,
                                        instruction=self._watch_run.instruction,
                                        fallback=fallback)
                elif self._busy and cls != "role-alert":
                    self._watch_run.pending_digest_reason = (
                        self._watch_run.pending_digest_reason or "copilot busy")
                else:
                    summary, cls = self._watch_fallback_renderable(st, d, target=target)
                    if summary is not None:
                        self._watch_chat(self._role(summary, cls))
            reason = self._watch_digest_reason()
            if reason:
                self._watch_emit_digest(reason=reason)
        timeline_changed = (d.new_events or d.status_from != d.status_to
                            or d.verdict_from != d.verdict_to
                            or d.new_changed or d.new_failures)
        if self.session.scope != SC.SESSION and timeline_changed:
            self._rebuild_timeline()
        elif d.new_events or d.status_from != d.status_to or d.verdict_from != d.verdict_to:
            self._timeline(_timeline_delta_line(st, d))
            agent = getattr(target, "agent", "") if target is not None else _agent_of(self.session)
            line = (_activity_line(st.last_record, _agent_hex(agent))
                    if st.last_record is not None else None)
            if line is not None:
                self._timeline(line)
        if self.session.scope == SC.SESSION:
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
        if low == "/goal" or low.startswith("/goal "):
            self.action_goal(cmd.strip()[5:].strip()); return
        if low == "/brief":
            self.action_brief(); return
        if low == "/check":
            self.action_check(); return
        if low == "/diff":
            self.action_diff(); return
        if low == "/status":
            self.action_status(); return
        if low == "/watch" or low.startswith("/watch "):
            self.action_watch(cmd.strip()[6:].strip()); return
        if low == "/sessions":
            self.action_sessions(); return
        if low == "/target":
            self.action_target(); return
        if low == "/here":
            if not self.session.switch_to_here():
                self.notify("no current Claude/Codex session detected",
                            severity="warning")
                return
            self._watch_scope_changed("attached session changed")
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
                before = self._evidence_sig()
                out = self.session.meta(cmd)
                self.notify(str(out).splitlines()[0])
                if self._evidence_sig() != before:
                    self._watch_scope_changed("evidence scope changed")
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
            self._watch_scope_changed("attached session changed")
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

    # ---- /goal: draft a paste-ready goal for the observed agent -------------
    def action_goal(self, instruction=""):
        self.session.refresh()
        if self._no_live():
            return
        det = _deterministic_goal(self.session.st, instruction)
        title = f"/goal — {self.session.scope_label()}"
        if instruction:
            title += f' · "{instruction}"'
        if not N.available(self.backend) or self._busy:
            self._result(det)
            self._update_status()
            return
        question = _goal_context_question(instruction)
        ctx = self.session.answer_context(question, history=list(self.session.history))
        self._ctx_stats = ctx.stats
        self._out_tokens = 0
        self._out_exact = False
        self._last_cost = None
        self.session.last_context_stats = ctx.stats
        self.session.last_output_tokens = 0
        self._busy = True
        self._busy_frame = 0
        self._chat(self._role(
            Text("🎯 drafting an agent /goal — grounded in agent + project context…",
                 style=_PAL["muted"]), "role-event"))
        self._update_status()
        self._goal_recap(title, ctx.text, det, instruction,
                         (self._evidence_sig(), self.session.store))

    @work(thread=True)
    def _goal_recap(self, title, ctx_text, det, instruction, origin):
        try:
            rec = N.goal_brief(ctx_text, model=self.model, backend=self.backend,
                               instruction=instruction)
            out = self.session._compose_goal(rec, det)
        except Exception as e:
            out = det + f"\n\n> _goal draft unavailable ({e}); deterministic draft above._"
        self.call_from_thread(self._goal_done, title, out, origin)

    def _goal_done(self, title, out, origin):
        self._busy = False
        self._busy_frame = 0
        sig, store = origin
        if self._evidence_sig() == sig and self.session.store is store:
            self._result(out)
        else:
            self.notify(f"dropped {title} — you switched while it ran",
                        severity="warning")
        self._update_status()
        self._drain_msg_queue()         # a message queued during /goal sends now

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
        self._watch_scope_changed("evidence selection changed")
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
            self._watch_scope_changed("cockpit conversation changed")
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
        if os.environ.get("TMUX"):
            if shutil.which("tmux"):
                try:
                    subprocess.run(["tmux", "load-buffer", "-w", "-"],
                                   input=text.encode("utf-8"), timeout=2,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                except Exception:
                    pass
            try:
                self._write_terminal_sequence(
                    _tmux_passthrough(_osc52_sequence(text)))
            except Exception:
                pass
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

    def _write_terminal_sequence(self, seq: str) -> None:
        driver = getattr(self, "_driver", None)
        if driver is not None:
            driver.write(seq)

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
