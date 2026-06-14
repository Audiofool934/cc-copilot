"""Codex adapter — read OpenAI Codex CLI session transcripts.

Codex persists one append-only JSONL "rollout" per session under
``$CODEX_HOME`` (default ``~/.codex``)::

    ~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-ts>-<uuid>.jsonl
    ~/.codex/archived_sessions/rollout-*.jsonl

Each line is an envelope ``{timestamp, type, payload}`` (verified against
codex 0.137.0):

- ``session_meta``  — ``payload.{id, cwd, model_provider, …}`` (the project cwd
  lives here, not in the path, unlike Claude Code's encoded directory).
- ``response_item`` — the canonical model items: ``message`` (role
  user/assistant/developer), ``reasoning``, ``function_call`` /
  ``function_call_output`` (shell), ``custom_tool_call`` /
  ``custom_tool_call_output`` (``apply_patch`` edits), web/tool search calls.
- ``event_msg``     — a parallel UI event stream (``task_started`` /
  ``task_complete`` / ``token_count`` / …) that *duplicates* the messages, so we
  parse it for metadata only and take records from ``response_item``.
- ``turn_context``  — per-turn ``cwd`` / ``approval_policy`` / model.

The adapter normalizes Codex's tool vocabulary into cc-copilot's canonical one
(``exec_command``→``Bash``, ``apply_patch``→per-file ``Edit``/``Write``) and
maps exit codes to ``is_error`` so the existing deterministic state folding
(commands, failures, changed files) works without change.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

from .. import locate
from ..transcript import (
    Record,
    Transcript,
    _flatten_content,
    _looks_like_human_prompt,
    _parse_ts,
    read_capped_lines,
)
from .base import AgentSource

# Bound the first-call discovery scan: a returning operator cares about recent
# sessions, and reading the head of thousands of rollouts on every cockpit poll
# would be wasteful. Most-recent-first by mtime; older sessions are not listed.
_MAX_SCAN = 800

# session_meta.id never changes, and the project cwd of a session is immutable,
# so head metadata can be cached by path with no invalidation.
_HEAD_CACHE: Dict[str, Tuple[str, str, bool]] = {}   # path -> (cwd, model, own)
_HEAD_LINKS: Dict[str, Tuple[str, str]] = {}         # path -> (forked_from, nickname)

# The directory walk that enumerates rollouts is the dominant discovery cost.
# A multi/project cockpit refresh calls list_sessions several times per poll, so
# cache the walk briefly per Codex home: this dedups the within-refresh calls and
# bounds the walk to once per TTL across the poll loop. Keyed by home so tests
# (each a fresh temp CODEX_HOME) never see another's entries. Staleness is at
# most TTL seconds — fine for a session board.
_ROLLOUTS_TTL = 2.0
_ROLLOUTS_CACHE: Dict[str, Tuple[float, List[Tuple[str, float]]]] = {}  # home -> (mono, items)

_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)
# Codex tool outputs report the real status on its own header line:
#   function_call_output:     "Process exited with code N"
#   custom_tool_call_output:  "Exit code: N"
# Anchor to the start of a line (and take the first match, which is the real
# wrapper header — it precedes any echoed stdout) so a command whose output
# merely contains "exited with code 1" isn't misread as a failure.
_EXIT_RE = re.compile(r"^(?:Process exited with code|Exit code:)\s*(-?\d+)", re.MULTILINE)

# Codex shell tools → cc-copilot's canonical command tool.
_SHELL_TOOLS = {"exec_command", "shell", "exec", "run_command", "local_shell"}
_CURRENT_ID_ENVS = ("CODEX_THREAD_ID", "CODEX_SESSION_ID",
                    "CODEX_CONVERSATION_ID", "CODEX_ROLLOUT_ID")


def codex_home() -> str:
    return os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex")


def _session_dirs() -> List[str]:
    home = codex_home()
    return [os.path.join(home, "sessions"), os.path.join(home, "archived_sessions")]


def _short(s: str, n: int = 220) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _session_id_from_name(path: str) -> str:
    m = _UUID_RE.search(os.path.basename(path))
    return m.group(1) if m else os.path.basename(path)[:-6]


# cc-copilot's own `codex exec` narration calls embed this preamble in their
# first message; the strings are read once from locate's signatures.
_OWN_SIG_STRS = tuple(s.decode("utf-8", "replace") for s in locate._OWN_SIGS)


def _head_meta(path: str) -> Tuple[str, str, bool]:
    """``(cwd, model, own)`` from the head of a rollout (cached, immutable).

    Read line by line rather than a fixed byte window: Codex's ``session_meta``
    line embeds the full ``base_instructions`` system prompt and is routinely
    larger than 16 KB, so a byte-capped read would truncate line 1 and miss the
    cwd. The cwd is on line 1; the own-signature is in the first few messages.
    """
    cached = _HEAD_CACHE.get(path)
    if cached is not None:
        return cached
    cwd, model, own = "", "", False
    forked_from, nickname = "", ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            # Bound per-line memory during discovery too: a corrupt/compacted
            # rollout can carry a multi-GB line among its head lines, which a
            # bare readline() would allocate whole before the capped parser runs.
            for n, (line, clipped) in enumerate(read_capped_lines(fh)):
                if n >= 24:
                    break
                if clipped:
                    continue
                if not own and any(sig in line for sig in _OWN_SIG_STRS):
                    own = True
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                p = obj.get("payload")
                if obj.get("type") == "session_meta" and isinstance(p, dict):
                    cwd = cwd or (p.get("cwd") or "")
                    model = model or (p.get("model_provider") or p.get("model") or "")
                    # cross-agent fan-out: a Codex thread forked from/parented by
                    # another (with a human-friendly nickname like "Mill").
                    forked_from = forked_from or (p.get("forked_from_id")
                                                  or p.get("parent_thread_id") or "")
                    nickname = nickname or (p.get("agent_nickname") or "")
                    # Codex subagent rollouts carry the lineage only under a
                    # nested source.subagent.thread_spawn block — fall back to it.
                    src = p.get("source")
                    sub = src.get("subagent") if isinstance(src, dict) else None
                    ts = sub.get("thread_spawn") if isinstance(sub, dict) else None
                    if isinstance(ts, dict):
                        forked_from = forked_from or (ts.get("parent_thread_id") or "")
                        nickname = nickname or (ts.get("agent_nickname") or "")
                elif obj.get("type") == "turn_context" and isinstance(p, dict):
                    cwd = cwd or (p.get("cwd") or "")
                    model = model or (p.get("model") or "")
    except OSError:
        pass
    _HEAD_CACHE[path] = (cwd, model, own)
    _HEAD_LINKS[path] = (str(forked_from or ""), str(nickname or ""))
    return cwd, model, own


def _head_links(path: str) -> Tuple[str, str]:
    """``(forked_from_id, nickname)`` for a rollout — the cross-agent fork link.
    Populated by :func:`_head_meta`'s single head read; trigger it if needed."""
    if path not in _HEAD_LINKS:
        _head_meta(path)
    return _HEAD_LINKS.get(path, ("", ""))


def _head_session_id(path: str) -> str:
    """Session id from the rollout head, for unusual filenames that don't carry it."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for n, (line, clipped) in enumerate(read_capped_lines(fh)):
                if n >= 24:
                    break
                if clipped:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = obj.get("payload") if isinstance(obj, dict) else None
                if obj.get("type") == "session_meta" and isinstance(payload, dict):
                    sid = payload.get("id")
                    return str(sid) if sid else ""
    except OSError:
        pass
    return ""


def _thread_names() -> Dict[str, str]:
    """``session_id -> thread_name`` from ``session_index.jsonl`` (cached by mtime)."""
    idx = os.path.join(codex_home(), "session_index.jsonl")
    try:
        mtime = os.path.getmtime(idx)
    except OSError:
        return {}
    cache = getattr(_thread_names, "_cache", None)
    if cache is not None and cache[0] == mtime:
        return cache[1]
    names: Dict[str, str] = {}
    try:
        with open(idx, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(o, dict) and o.get("id"):
                    n = o.get("thread_name")
                    if isinstance(n, str) and n.strip():
                        names[str(o["id"])] = n.strip()
    except OSError:
        return {}
    _thread_names._cache = (mtime, names)  # type: ignore[attr-defined]
    return names


def _iter_rollouts() -> List[Tuple[str, float]]:
    """All rollout files across the session dirs as ``(path, mtime)``, newest
    first. Cached per Codex home for a short TTL (see ``_ROLLOUTS_CACHE``)."""
    home = codex_home()
    cached = _ROLLOUTS_CACHE.get(home)
    if cached is not None and (time.monotonic() - cached[0]) < _ROLLOUTS_TTL:
        return cached[1]
    out: List[Tuple[str, float]] = []
    for root in _session_dirs():
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if not (name.startswith("rollout-") and name.endswith(".jsonl")):
                    continue
                p = os.path.join(dirpath, name)
                try:
                    out.append((p, os.path.getmtime(p)))
                except OSError:
                    continue
    out.sort(key=lambda t: t[1], reverse=True)
    _ROLLOUTS_CACHE[home] = (time.monotonic(), out)
    return out


class CodexSource(AgentSource):
    name = "codex"
    label = "Codex"

    def available(self) -> bool:
        return any(os.path.isdir(d) for d in _session_dirs())

    def current_session_id(self) -> str:
        for name in _CURRENT_ID_ENVS:
            sid = os.environ.get(name, "").strip()
            if sid:
                return sid
        return ""

    def current_session_path(self) -> Optional[str]:
        sid = self.current_session_id()
        if sid:
            matches = []
            for path, mtime in _iter_rollouts():
                base = os.path.basename(path)
                if (sid in base or _session_id_from_name(path) == sid
                        or _head_session_id(path) == sid):
                    matches.append((mtime, path))
            if matches:
                return max(matches)[1]

        # Last-resort "inside Codex but no explicit id" fallback: attach to the
        # newest rollout for the current cwd. This is intentionally gated on a
        # Codex env marker so a plain terminal `--here` doesn't silently become
        # "latest session in this repo".
        if not any(k.startswith("CODEX_") for k in os.environ):
            return None
        cwd = os.path.abspath(os.getcwd())
        refs = self.list_sessions(cwd, include_own=True)
        return refs[0].path if refs else None

    def owns(self, path: str) -> bool:
        base = os.path.basename(path)
        if base.startswith("rollout-") and base.endswith(".jsonl"):
            return True
        return (os.sep + ".codex" + os.sep) in path and base.endswith(".jsonl")

    # ---- discovery ------------------------------------------------------
    def list_sessions(self, cwd: str, include_own: bool = False) -> List[locate.SessionRef]:
        target = os.path.abspath(cwd) if cwd else ""
        refs: List[locate.SessionRef] = []
        for path, mtime in _iter_rollouts()[:_MAX_SCAN]:
            scwd, model, own = _head_meta(path)
            if not scwd or os.path.abspath(scwd) != target:
                continue
            if own and not include_own:
                continue
            sid = _session_id_from_name(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            forked_from, nickname = _head_links(path)
            refs.append(locate.SessionRef(
                path=path, session_id=sid, mtime=mtime, size=size,
                title=_thread_names().get(sid, ""), own=own,
                agent=self.name, model=model,
                nickname=nickname, forked_from=forked_from,
            ))
        return refs

    def projects_with_sessions(self, limit: int = 8) -> List[Tuple[str, int, float]]:
        agg: Dict[str, Tuple[str, int, float]] = {}
        for path, mtime in _iter_rollouts()[:_MAX_SCAN]:
            scwd, _model, own = _head_meta(path)
            if not scwd or own:
                continue
            key = os.path.abspath(scwd)
            if key in agg:
                _c, n, m = agg[key]
                agg[key] = (scwd, n + 1, max(m, mtime))
            else:
                agg[key] = (scwd, 1, mtime)
        out = sorted(agg.values(), key=lambda x: -x[2])
        return out[:limit]

    def read_cwd(self, path: str) -> Optional[str]:
        cwd, _m, _o = _head_meta(path)
        return cwd or None

    def read_title(self, path: str, session_id: str = "") -> str:
        sid = session_id or _session_id_from_name(path)
        return _thread_names().get(sid, "")

    # ---- parse ----------------------------------------------------------
    def parse(self, path: str) -> Transcript:
        tr = Transcript(path=path)
        # apply_patch expands into one synthetic call/result per file; remember
        # how many a given call_id produced so its output can pair back up.
        patch_arity: Dict[str, int] = {}
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            return tr
        with fh:
            # Bound per-line memory: a Codex Compacted.replacement_history blob
            # or a corrupt rollout can be a single multi-GB line (issue #24948).
            for i, (raw, clipped) in enumerate(read_capped_lines(fh), start=1):
                if clipped:
                    tr.raw_lines += 1
                    tr.parse_errors += 1
                    continue
                raw = raw.strip()
                if not raw:
                    continue
                tr.raw_lines += 1
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    tr.parse_errors += 1
                    continue
                if isinstance(obj, dict):
                    self._ingest(tr, i, obj, patch_arity)
        return tr

    def _ingest(self, tr: Transcript, line: int, obj: dict,
                patch_arity: Dict[str, int]) -> None:
        ts = _parse_ts(obj.get("timestamp"))
        if ts is not None:
            if tr.first_seen_ts is None or ts < tr.first_seen_ts:
                tr.first_seen_ts = ts
            if tr.last_seen_ts is None or ts > tr.last_seen_ts:
                tr.last_seen_ts = ts
        typ = obj.get("type")
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            return
        raw_ts = obj.get("timestamp", "") if isinstance(obj, dict) else ""

        if typ == "session_meta":
            if payload.get("id"):
                tr.session_id = str(payload["id"])
            if payload.get("cwd"):
                tr.cwd = str(payload["cwd"])
            if payload.get("cli_version"):
                tr.version = str(payload["cli_version"])
            g = payload.get("git")
            if isinstance(g, dict) and g.get("branch"):
                # latest session_meta wins — Codex re-emits it, and the branch can
                # change mid-session (a checkout), same as Claude's latest-wins.
                tr.git_branch = str(g["branch"])
            return

        if typ == "turn_context":
            if not tr.cwd and payload.get("cwd"):
                tr.cwd = str(payload["cwd"])
            if not tr.permission_mode and payload.get("approval_policy"):
                tr.permission_mode = str(payload["approval_policy"])
            # Record the per-turn autonomy context so a mid-session escalation
            # (sandbox widened, approval loosened) is detectable. sandbox_policy
            # is an object whose `type` is the privilege level.
            sp = payload.get("sandbox_policy")
            sandbox = sp.get("type") if isinstance(sp, dict) else (sp or None)
            net = sp.get("network_access") if isinstance(sp, dict) else None
            tr.turn_contexts.append({
                "line": line,
                "sandbox": sandbox,
                "network": net,
                "approval": payload.get("approval_policy"),
                "model": payload.get("model"),
            })
            return

        if typ == "event_msg":
            # event_msg DUPLICATES response_item message/reasoning content, so we
            # don't take records from it — EXCEPT the control variants that exist
            # only here: a turn that ended abnormally (aborted/interrupted) or a
            # surfaced error. Captured as `system` records with Codex-specific
            # levels so assess can cite them; everything else is still skipped.
            ev = payload.get("type")
            if ev == "turn_aborted":
                reason = str(payload.get("reason") or "interrupted")
                tr.records.append(Record(line, "system", ts, raw_ts,
                                         level="codex_turn_aborted",
                                         text=f"turn aborted: {reason}"))
            elif ev == "error":
                msg = _short(str(payload.get("message") or "error"), 200)
                tr.records.append(Record(line, "system", ts, raw_ts,
                                         level="codex_error", text=msg))
            elif ev == "token_count":
                # Exact agent-side usage (latest wins). CONTEXT occupancy is
                # last_token_usage — total_token_usage is cumulative across the
                # session and exceeds the window, so it is NOT a fullness ratio.
                info = payload.get("info") or {}
                last = info.get("last_token_usage") or {}
                rl = (payload.get("rate_limits") or {}).get("primary") or {}
                if info or payload.get("rate_limits"):
                    tr.token_usage = {
                        "context_tokens": last.get("total_tokens"),
                        "context_window": info.get("model_context_window"),
                        "rate_primary_pct": rl.get("used_percent"),
                        "line": line,
                    }
            return

        if typ != "response_item":
            return  # other top-level types carry no record content

        pt = payload.get("type")

        if pt == "message":
            role = payload.get("role")
            text = _flatten_content(payload.get("content"))
            if role == "assistant":
                if text.strip():
                    tr.records.append(Record(line, "agent_text", ts, raw_ts, text=text))
            elif role == "user":
                if _looks_like_human_prompt(text):
                    tr.records.append(Record(line, "human", ts, raw_ts, text=text))
            # role == "developer" → injected instructions; not a human turn
            return

        if pt == "reasoning":
            text = _flatten_content(payload.get("summary"))
            if text.strip():
                tr.records.append(Record(line, "agent_thinking", ts, raw_ts, text=text))
            return

        if pt in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            self._ingest_tool_call(tr, line, ts, raw_ts, pt, payload, patch_arity)
            return

        if pt in ("function_call_output", "custom_tool_call_output", "tool_search_output"):
            self._ingest_tool_result(tr, line, ts, raw_ts, payload, patch_arity)
            return

    # ---- tool calls -----------------------------------------------------
    def _ingest_tool_call(self, tr, line, ts, raw_ts, pt, payload, patch_arity):
        call_id = str(payload.get("call_id") or "")
        name = payload.get("name") or ""

        if pt == "web_search_call":
            tr.records.append(Record(line, "tool_call", ts, raw_ts,
                                     tool_id=call_id, tool_name="WebSearch",
                                     tool_input=_as_dict(payload.get("action"))))
            return
        if pt == "tool_search_call":
            tr.records.append(Record(line, "tool_call", ts, raw_ts,
                                     tool_id=call_id, tool_name="ToolSearch",
                                     tool_input=_as_dict(payload.get("arguments"))))
            return

        if pt == "custom_tool_call" and name == "apply_patch":
            files = _patch_files(payload.get("input") or "")
            if not files:
                # no parseable file ops — a single generic call that pairs with
                # its output by the bare call_id (do NOT register patch arity,
                # or the output would be emitted under call_id#0 and never pair)
                tr.records.append(Record(line, "tool_call", ts, raw_ts,
                                         tool_id=call_id, tool_name="Edit", tool_input={}))
                return
            for idx, (tool, fpath) in enumerate(files):
                tr.records.append(Record(
                    line, "tool_call", ts, raw_ts,
                    tool_id=f"{call_id}#{idx}", tool_name=tool,
                    tool_input={"file_path": fpath},
                ))
            patch_arity[call_id] = len(files)
            return

        if pt == "function_call" and name in _SHELL_TOOLS:
            args = _as_dict(payload.get("arguments"))
            tr.records.append(Record(
                line, "tool_call", ts, raw_ts,
                tool_id=call_id, tool_name="Bash",
                tool_input={"command": _shell_command(args),
                            "description": str(args.get("workdir", ""))},
            ))
            return

        if pt == "function_call" and name == "update_plan":
            # Codex's plan tool == Claude Code's TodoWrite. Normalize to the same
            # shape so the deterministic state surfaces the agent's plan.
            args = _as_dict(payload.get("arguments"))
            plan = args.get("plan")
            todos = [{"content": str(s.get("step", "")), "status": str(s.get("status", ""))}
                     for s in plan if isinstance(s, dict)] if isinstance(plan, list) else []
            tr.records.append(Record(
                line, "tool_call", ts, raw_ts,
                tool_id=call_id, tool_name="TodoWrite",
                tool_input={"todos": todos},
            ))
            return

        # any other tool (MCP, custom) — keep its name so tool_counts is honest
        tr.records.append(Record(
            line, "tool_call", ts, raw_ts,
            tool_id=call_id, tool_name=str(name or pt),
            tool_input=_as_dict(payload.get("arguments") or payload.get("input")),
        ))

    def _ingest_tool_result(self, tr, line, ts, raw_ts, payload, patch_arity):
        call_id = str(payload.get("call_id") or "")
        out = payload.get("output")
        text = _normalize_output(out)          # flatten image/structured blocks first
        is_err = _exit_failed(text)            # decide failure from the full wrapper…
        body = _short(_clean_output(text))     # …then show just the real output body
        arity = patch_arity.get(call_id)
        if arity:  # an apply_patch that expanded into per-file calls
            for idx in range(arity):
                tr.records.append(Record(
                    line, "tool_result", ts, raw_ts,
                    tool_id=f"{call_id}#{idx}", is_error=is_err, text=body,
                ))
            return
        tr.records.append(Record(
            line, "tool_result", ts, raw_ts,
            tool_id=call_id, is_error=is_err, text=body,
        ))


# ---- helpers ------------------------------------------------------------
def _as_dict(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _shell_command(args: dict) -> str:
    """The command string from a Codex shell call's arguments."""
    cmd = args.get("cmd")
    if cmd is None:
        cmd = args.get("command")
    if isinstance(cmd, list):
        parts = [str(c) for c in cmd]
        # ["bash","-lc","<real command>"] → the real command
        if len(parts) >= 3 and parts[0] in ("bash", "sh", "zsh") and parts[1] in ("-lc", "-c"):
            return parts[2]
        return " ".join(parts)
    return str(cmd or "")


def _exit_failed(output: str) -> bool:
    """True if a tool output reports a non-zero exit code."""
    m = _EXIT_RE.search(output or "")
    if not m:
        return False
    try:
        return int(m.group(1)) != 0
    except ValueError:
        return False


def _normalize_output(out) -> str:
    """Codex tool output is usually a string, but image/structured tools return
    a list of content blocks. Flatten blocks to their text and substitute a
    placeholder for image/binary blocks so multi-KB base64 image_url data never
    lands verbatim in a brief; a string passes through unchanged.
    """
    if isinstance(out, str):
        return out
    if out is None:
        return ""
    if isinstance(out, list):
        parts = []
        for b in out:
            if not isinstance(b, dict):
                parts.append(str(b))
                continue
            kind = b.get("type") or ""
            if "image" in kind:
                parts.append("[image]")
            elif b.get("text") is not None:
                parts.append(str(b.get("text")))
            else:
                parts.append(f"[{kind or 'content'}]")
        return "\n".join(p for p in parts if p)
    return json.dumps(out, ensure_ascii=False)


def _clean_output(text: str) -> str:
    """Strip Codex's tool-output wrapper, keeping just the real output body.

    Codex wraps shell/patch output with a header (``Chunk ID`` / ``Wall time`` /
    ``Process exited with code N`` / ``Output:``). The status is parsed from the
    full wrapper by :func:`_exit_failed`; for *display* (briefs, since, context)
    the body after ``Output:`` is what matters. Falls back to the original text
    when there's no recognizable wrapper.
    """
    if not text:
        return text
    marker = "\nOutput:\n"
    idx = text.find(marker)
    if idx != -1:
        body = text[idx + len(marker):]
    elif text.startswith("Output:\n"):
        body = text[len("Output:\n"):]
    else:
        return text
    head, _, rest = body.partition("\n")
    if head.startswith("Total output lines:"):   # a metadata line Codex sometimes adds
        body = rest
    # a recognized wrapper with an empty body means the command truly produced no
    # output — return empty, not the wrapper (the `else` paths above keep the
    # original text when there's no wrapper at all)
    return body.strip()


_PATCH_OP = re.compile(r"^\*\*\*\s+(Add|Update|Delete)\s+File:\s*(.+?)\s*$")
_PATCH_MOVE = re.compile(r"^\*\*\*\s+Move\s+to:\s*(.+?)\s*$")


def _patch_files(patch: str) -> List[Tuple[str, str]]:
    """``[(tool, path)]`` for each file op in an apply_patch envelope.

    Add → ``Write`` (new file); Update/Delete → ``Edit`` (existing change).
    """
    out: List[Tuple[str, str]] = []
    for raw in (patch or "").splitlines():
        m = _PATCH_OP.match(raw)
        if m:
            verb, fpath = m.group(1), m.group(2)
            tool = "Write" if verb == "Add" else "Edit"
            out.append((tool, fpath))
            continue
        mv = _PATCH_MOVE.match(raw)
        if mv and out:  # a rename target for the preceding Update
            out[-1] = (out[-1][0], mv.group(1))
    return out
