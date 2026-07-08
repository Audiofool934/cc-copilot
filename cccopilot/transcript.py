"""Parse a Claude Code session JSONL transcript into normalized records.

Claude Code writes one JSON object per line to
``~/.claude/projects/<encoded-cwd>/<session-id>.jsonl``. The shapes we rely on
(verified against real transcripts):

- ``type: "user"`` with ``message.content`` either a **string** (a real human
  prompt) or a **list** of blocks (``tool_result``, ``text``, ``image``, ...).
- ``type: "assistant"`` with ``message.content`` a list of blocks:
  ``thinking`` / ``text`` / ``tool_use`` (``{id, name, input}``).
- ``tool_result`` blocks carry ``tool_use_id`` and ``is_error`` and a
  ``content`` that is a str or a list of ``{type:text, text}`` blocks.
- Top-level fields seen on most lines: ``timestamp``, ``cwd``, ``gitBranch``,
  ``version``, ``uuid``, ``sessionId``. Other line ``type``s include
  ``file-history-snapshot``, ``system``, ``mode``, ``permission-mode``.

We do not assume a field exists; anything missing degrades gracefully.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


# Tools that mutate the working tree (used by the state builder to decide what
# counts as a "change" vs. a read-only probe).
MUTATING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READONLY_TOOLS = {"Read", "Grep", "Glob", "ToolSearch", "WebFetch", "WebSearch"}

# Client-side / housekeeping slash commands: handled locally by the CLI, they
# never reach the model, so they are neither a substantive "ask" nor something
# the agent owes a reply to. We recover them (so we don't misread them) but tag
# them so they don't pollute intents or anchor the status as "awaiting-agent".
LOCAL_COMMANDS = {
    "clear", "compact", "config", "cost", "help", "login", "logout", "mcp",
    "model", "plugin", "resume", "status", "doctor", "exit", "quit", "bug",
    "release-notes", "memory", "permissions", "vim", "agents", "ide", "privacy",
    "upgrade", "context", "terminal-setup", "hooks", "output-style", "add-dir",
    "install-github-app", "todos", "fast", "effort", "theme", "statusline",
    "feedback", "approved-tools", "mcp-debug",
}


@dataclass
class Record:
    """One addressable thing that happened, normalized from a transcript line.

    ``line`` is the 1-based JSONL line number so any claim can be re-verified
    with e.g. ``sed -n '142p' session.jsonl``. ``kind`` is one of:
    ``human``, ``agent_text``, ``agent_thinking``, ``tool_call``,
    ``tool_result``, ``snapshot``, ``system``.
    """

    line: int
    kind: str
    ts: Optional[datetime] = None
    raw_ts: str = ""
    # kind-specific payload
    text: str = ""              # human / agent_text / agent_thinking / system
    tool_id: str = ""           # tool_call / tool_result
    tool_name: str = ""         # tool_call
    tool_input: dict = field(default_factory=dict)  # tool_call
    is_error: bool = False      # tool_result
    level: str = ""             # system
    housekeeping: bool = False  # human: a local/housekeeping slash command

    @property
    def hhmm(self) -> str:
        return self.ts.strftime("%H:%M") if self.ts else "--:--"


@dataclass
class Transcript:
    path: str
    session_id: str = ""
    cwd: str = ""
    git_branch: str = ""
    version: str = ""
    permission_mode: str = ""
    title: str = ""
    title_is_custom: bool = False   # a name the human set beats the auto ai-title
    records: list = field(default_factory=list)
    raw_lines: int = 0
    parse_errors: int = 0
    # min/max timestamp across *all* lines (incl. metadata) — for "span".
    first_seen_ts: Optional[datetime] = None
    last_seen_ts: Optional[datetime] = None
    # Latest agent-reported token usage / rate-limit, if the source exposes it
    # (Codex token_count). Exact, not estimated. None for sources that don't.
    token_usage: Optional[dict] = None
    # Per-turn autonomy context (Codex turn_context): the sandbox/approval/model
    # in effect for each turn, in order — so a mid-session escalation is visible.
    turn_contexts: list = field(default_factory=list)

    @property
    def first_ts(self) -> Optional[datetime]:
        """First timestamp on an actual activity record (not metadata)."""
        for r in self.records:
            if r.ts:
                return r.ts
        return None

    @property
    def last_ts(self) -> Optional[datetime]:
        """Last activity timestamp — the basis for 'how long ago' / idle."""
        for r in reversed(self.records):
            if r.ts:
                return r.ts
        return self.last_seen_ts


def _parse_ts(s: Any) -> Optional[datetime]:
    if not isinstance(s, str) or not s:
        return None
    try:
        # "2026-03-19T14:55:14.320Z" -> aware UTC, then to local for display.
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _flatten_content(content: Any) -> str:
    """tool_result / block content may be a str or a list of {type,text}."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                if isinstance(b.get("text"), str):
                    out.append(b["text"])
                elif b.get("type") == "image":
                    out.append("[image]")
                else:
                    out.append(json.dumps(b, ensure_ascii=False)[:200])
            else:
                out.append(str(b))
        return "\n".join(out)
    return "" if content is None else str(content)


def _looks_like_human_prompt(text: str) -> bool:
    """A user string that is an actual human message, not a harness wrapper.

    Claude Code injects command stdout, local-command wrappers, and
    ``<system-reminder>`` blocks as user-role strings; those start with a tag
    or are command plumbing. We keep genuine prose. (Slash-command invocations
    are recovered separately by :func:`_parse_slash_command` before this gate.)
    """
    t = text.strip()
    if not t:
        return False
    if t.startswith("<"):  # <system-reminder>, <command-name>, <local-command-...>
        return False
    if t.startswith("Caveat:"):
        return False
    return True


_CMD_NAME = re.compile(r"<command-name>\s*(/?[^<\s]+)\s*</command-name>")
_CMD_ARGS = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.S)


def _parse_slash_command(text: str) -> Optional[str]:
    """Recover the user's *actual* slash-command action from its wrapper.

    Claude Code records a slash command as ``<command-name>/foo</command-name>``
    (+ optional ``<command-args>``), and *separately* injects an ``isMeta`` user
    message containing the command's expanded template body. We surface the
    former (the user really did invoke ``/foo``) and drop the latter (template
    plumbing the user never typed). Returns e.g. ``"/codex:review --base main"``.
    """
    m = _CMD_NAME.search(text)
    if not m:
        return None
    name = m.group(1).strip()
    a = _CMD_ARGS.search(text)
    cargs = a.group(1).strip() if a else ""
    return (name + (" " + cargs if cargs else "")).strip()


# A single JSONL line longer than this is pathological — a multi-MB tool_result,
# a Codex Compacted.replacement_history blob, or a corrupt rollout (Codex issue
# #24948 produces multi-GB rollouts). We read up to the cap and DISCARD the rest
# of that physical line in fixed chunks, so the cockpit's memory stays bounded
# instead of buffering the whole line. Well above any legitimate line (Codex's
# session_meta with embedded base_instructions is routinely >16 KB, never ~1 MB).
MAX_LINE_CHARS = 1_000_000


def read_capped_lines(fh, cap: int = MAX_LINE_CHARS):
    """Yield ``(text, clipped)`` per physical line of ``fh`` (read-only) without
    ever buffering more than ``cap`` chars of one line. ``clipped`` is True when
    the line exceeded the cap — its tail is drained chunk-by-chunk and discarded,
    so a pathological multi-GB line can't exhaust memory. Line numbering is
    preserved (one yield per physical line), so ``[L<n>]`` citations stay valid.
    """
    while True:
        chunk = fh.readline(cap)
        if not chunk:
            return
        if chunk.endswith("\n") or len(chunk) < cap:
            yield chunk, False
            continue
        # Hit the cap with no newline: drain the rest of this line and drop it.
        while True:
            rest = fh.readline(cap)
            if not rest or rest.endswith("\n"):
                break
        yield chunk, True


def parse(path: str) -> Transcript:
    tr = Transcript(path=path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, (line, clipped) in enumerate(read_capped_lines(fh), start=1):
            if clipped:                       # over the per-line cap; can't parse
                tr.raw_lines += 1
                tr.parse_errors += 1
                continue
            line = line.strip()
            if not line:
                continue
            tr.raw_lines += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                tr.parse_errors += 1
                continue
            _ingest(tr, i, obj)
    return tr


def _ingest(tr: Transcript, line: int, obj: dict) -> None:
    typ = obj.get("type")
    ts = _parse_ts(obj.get("timestamp"))
    if ts is not None:
        if tr.first_seen_ts is None or ts < tr.first_seen_ts:
            tr.first_seen_ts = ts
        if tr.last_seen_ts is None or ts > tr.last_seen_ts:
            tr.last_seen_ts = ts

    # Capture session-level metadata wherever it appears (latest wins).
    if obj.get("sessionId"):
        tr.session_id = obj["sessionId"]
    if obj.get("cwd"):
        tr.cwd = obj["cwd"]
    if obj.get("gitBranch"):
        tr.git_branch = obj["gitBranch"]
    if obj.get("version"):
        tr.version = obj["version"]
    if typ in ("permission-mode", "mode") and obj.get("permissionMode"):
        tr.permission_mode = obj["permissionMode"]
    if typ == "custom-title":
        t = obj.get("customTitle")
        if isinstance(t, str) and t.strip():
            tr.title = t.strip()            # the human's own name
            tr.title_is_custom = True
    elif typ == "ai-title":
        t = obj.get("aiTitle")
        if isinstance(t, str) and t.strip() and not tr.title_is_custom:
            tr.title = t.strip()            # auto-title only if no custom name set

    # Harness-injected, NOT the human's/agent's own words. A returning person
    # must never see these as "your asks" or "agent's last words":
    #   isMeta          — resume stubs ("Continue from where you left off."),
    #                     slash-command template expansions
    #   isCompactSummary — auto-generated context-compaction summaries
    is_injected = bool(obj.get("isMeta")) or bool(obj.get("isCompactSummary"))

    msg = obj.get("message")

    if typ == "user":
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            if not is_injected:
                cmd = _parse_slash_command(content)
                if cmd:
                    # the user's real action: they invoked /foo (we drop the
                    # separate isMeta line carrying the expanded template body)
                    parts = cmd.lstrip("/").split()
                    base = parts[0].lower() if parts else ""
                    tr.records.append(Record(line, "human", ts, _raw(obj),
                                             text=cmd, housekeeping=base in LOCAL_COMMANDS))
                elif _looks_like_human_prompt(content):
                    tr.records.append(Record(line, "human", ts, _raw(obj), text=content))
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    tr.records.append(Record(
                        line, "tool_result", ts, _raw(obj),
                        tool_id=b.get("tool_use_id", ""),
                        is_error=bool(b.get("is_error")),
                        text=_flatten_content(b.get("content")),
                    ))
                elif (b.get("type") == "text" and not is_injected
                      and _looks_like_human_prompt(b.get("text", ""))):
                    tr.records.append(Record(line, "human", ts, _raw(obj), text=b.get("text", "")))
        return

    if typ == "assistant":
        content = msg.get("content") if isinstance(msg, dict) else None
        # ``model: "<synthetic>"`` marks a harness-generated placeholder
        # ("No response requested." after /clear, etc.) — never the agent.
        synthetic = isinstance(msg, dict) and msg.get("model") == "<synthetic>"
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and not synthetic:
                    tr.records.append(Record(line, "agent_text", ts, _raw(obj), text=b.get("text", "")))
                elif bt == "thinking" and not synthetic:
                    tr.records.append(Record(line, "agent_thinking", ts, _raw(obj), text=b.get("thinking", "")))
                elif bt == "tool_use":
                    tr.records.append(Record(
                        line, "tool_call", ts, _raw(obj),
                        tool_id=b.get("id", ""),
                        tool_name=b.get("name", ""),
                        tool_input=b.get("input") if isinstance(b.get("input"), dict) else {},
                    ))
        return

    if typ == "file-history-snapshot":
        tr.records.append(Record(line, "snapshot", ts, _raw(obj)))
        return

    if typ == "system":
        tr.records.append(Record(
            line, "system", ts, _raw(obj),
            level=str(obj.get("level", "")),
            text=_flatten_content(obj.get("content")),
        ))
        return


def _raw(obj: dict) -> str:
    return obj.get("timestamp", "") if isinstance(obj, dict) else ""
