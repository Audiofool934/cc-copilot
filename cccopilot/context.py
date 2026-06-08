"""Question-aware evidence context for model-backed answers.

The deterministic ``brief`` remains the compact human surface. This module is
the v0.7 conversational path: build a model context from primary observable
evidence first, then include summaries only as navigation/index material.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from . import scope as SC, state as S, transcript as T, sources as SRC
from .brief import _dur


DEFAULT_CONTEXT_TOKENS = 60000
RECENT_TAIL_RECORDS = 14
KEYWORD_MATCH_RECORDS = 18
LINE_WINDOW_RADIUS = 1
PER_RECORD_CHARS = 24000
PROJECT_CONTEXT_CHARS = 36000
PROJECT_EXCERPT_LINES = 80
PROJECT_INDEX_FILES = 120

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "did",
    "do", "does", "for", "from", "have", "how", "i", "in", "is", "it",
    "me", "of", "on", "or", "our", "please", "should", "so", "that",
    "the", "this", "to", "we", "what", "when", "where", "which", "who",
    "why", "with", "you",
}

_CITATION = re.compile(r"\[(?:(?P<sid>[A-Za-z0-9_.-]+):)?L(?P<line>\d+)[^\]]*\]")
_BARE_LINE = re.compile(r"\bL(?P<line>\d+)\b")


@dataclass
class ContextStats:
    estimated_tokens: int = 0
    raw_tokens: int = 0
    project_tokens: int = 0
    chat_tokens: int = 0
    memory_tokens: int = 0
    index_tokens: int = 0
    raw_records: int = 0
    raw_candidates: int = 0
    budget_tokens: int = DEFAULT_CONTEXT_TOKENS
    truncated: bool = False


@dataclass
class EvidenceContext:
    text: str
    stats: ContextStats


@dataclass
class _Source:
    path: str
    st: Optional[S.State]
    session_id: str
    label: str
    records: list = field(default_factory=list)
    by_line: dict = field(default_factory=dict)
    call_by_id: dict = field(default_factory=dict)
    result_by_id: dict = field(default_factory=dict)


@dataclass
class _Selected:
    source: _Source
    record: T.Record
    priority: int
    reasons: set = field(default_factory=set)


def estimate_tokens(text: str) -> int:
    """Small local estimate used before exact backend usage exists."""
    return max(1, (len(text or "") + 3) // 4)


def format_hud(stats: ContextStats, output_tokens: int = None) -> str:
    """Compact context usage line for TUI/CLI surfaces."""
    parts = [
        f"ctx ~{_tok(stats.estimated_tokens)} / {_tok(stats.budget_tokens)}",
        f"raw {_tok(stats.raw_tokens)}",
        f"project {_tok(stats.project_tokens)}",
        f"chat {_tok(stats.chat_tokens)}",
        f"memory {_tok(stats.memory_tokens)}",
        f"index {_tok(stats.index_tokens)}",
    ]
    if output_tokens is not None:
        parts.insert(1, f"out ~{_tok(output_tokens)}")
    if stats.truncated:
        parts.append("trimmed")
    return " · ".join(parts)


def format_answering(stats: ContextStats, output_tokens: int = 0) -> str:
    raw_pct = int(round(100 * stats.raw_tokens / max(1, stats.estimated_tokens)))
    return (f"in ~{_tok(stats.estimated_tokens)} · out ~{_tok(output_tokens)} · "
            f"window {_tok(stats.budget_tokens)} · raw {raw_pct}%")


def _tok(n: int) -> str:
    n = max(0, int(n or 0))
    if n < 1000:
        return str(n)
    if n < 10000:
        s = f"{n / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{s}k"
    return f"{round(n / 1000)}k"


def chat_history_budget_chars(max_tokens: int = None) -> int:
    max_tokens = _context_token_budget(max_tokens)
    max_chars = max_tokens * 4
    return max(4000, min(max_chars // 5, 36000))


def build(path: str, st=None, scope: str = SC.SESSION, sessions=None,
          question: str = "", history=None, project_context: bool = True,
          max_tokens: int = None, memory_text: str = "") -> EvidenceContext:
    """Assemble a question-aware evidence context pack.

    Raw transcript records are primary. The rendered deterministic recap is
    included last as an orientation index, not as the model's only source.
    """
    sc = SC.normalize(scope)
    history = list(history or [])
    max_tokens = _context_token_budget(max_tokens)
    max_chars = max_tokens * 4
    sources = _sources(path, st, sc, sessions)
    selectors = SC.parse_selectors(sessions)
    terms = _terms(question)

    selected = _select_records(sources, question, history, terms)
    raw_text = _render_raw_records(selected)
    project_text = _render_project_context(path, st, sc, project_context, terms)
    index_text = _render_summary_index(path, st, sc, selectors)
    memory = _render_memory(memory_text)
    chat_text = _render_chat_history(history, max_chars=chat_history_budget_chars(max_tokens))
    status_text = _render_status(path, st, sc, selectors, sources, terms, selected)

    stats = ContextStats(
        budget_tokens=max_tokens,
        raw_tokens=estimate_tokens(raw_text),
        project_tokens=estimate_tokens(project_text) if project_text else 0,
        chat_tokens=estimate_tokens(chat_text) if chat_text else 0,
        memory_tokens=estimate_tokens(memory) if memory else 0,
        index_tokens=estimate_tokens(index_text) if index_text else 0,
        raw_records=len(selected),
        raw_candidates=sum(len(src.records) for src in sources),
    )

    sections = [
        ("status", status_text),
        ("memory", memory),
        ("chat", chat_text),
        ("raw", raw_text),
        ("project", project_text),
        ("index", index_text),
    ]
    body, truncated = _pack_sections(sections, max_chars=max_chars)
    stats.truncated = truncated
    stats.estimated_tokens = estimate_tokens(body)
    return EvidenceContext(body, stats)


def _context_token_budget(value: int = None) -> int:
    if value:
        return max(4000, int(value))
    raw = os.environ.get("CC_COPILOT_CONTEXT_TOKENS", "").strip()
    if raw.isdigit():
        return max(4000, int(raw))
    return DEFAULT_CONTEXT_TOKENS


def _sources(path: str, st, scope: str, sessions) -> list:
    if scope == SC.SESSION:
        current = _load_current(path, st)
        return [current] if current is not None else []
    out = []
    here = os.path.abspath(path) if path else ""
    for ref in SC.resolve_session_refs(path, SC.parse_selectors(sessions)):
        try:
            rst = st if here and os.path.abspath(ref.path) == here and st is not None \
                else S.build(SRC.parse(ref.path))
        except Exception:
            continue
        out.append(_source(ref.path, rst, ref.session_id))
    return out


def _load_current(path: str, st) -> Optional[_Source]:
    if st is None:
        if not path or not os.path.isfile(path):
            return None
        st = S.build(SRC.parse(path))
    sid = getattr(st.tr, "session_id", "") or (os.path.basename(path or "")[:-6])
    return _source(path, st, sid)


def _source(path: str, st, session_id: str = "") -> _Source:
    sid = session_id or getattr(st.tr, "session_id", "") or os.path.basename(path or "")[:-6]
    label = (sid[:8] or "session")
    src = _Source(path=path, st=st, session_id=sid, label=label,
                  records=list(getattr(st.tr, "records", []) if st is not None else []))
    src.by_line = {r.line: r for r in src.records}
    src.call_by_id = {r.tool_id: r for r in src.records
                      if r.kind == "tool_call" and r.tool_id}
    src.result_by_id = {r.tool_id: r for r in src.records
                        if r.kind == "tool_result" and r.tool_id}
    return src


def _terms(question: str) -> list:
    raw = re.findall(r"[\w./:-]+", (question or "").lower(), flags=re.UNICODE)
    out, seen = [], set()
    for term in raw:
        term = term.strip("_-:./")
        if not term:
            continue
        if term in _STOPWORDS:
            continue
        if len(term) < 3 and all(ord(ch) < 128 for ch in term):
            continue
        if term not in seen:
            seen.add(term)
            out.append(term)
    return out[:24]


def _select_records(sources: list, question: str, history: list, terms: list) -> dict:
    selected: dict[tuple, _Selected] = {}

    def add(src: _Source, record, priority: int, reason: str, window: int = 0):
        if record is None:
            return
        key = (src.label, record.line, record.kind, record.tool_id)
        item = selected.get(key)
        if item is None:
            item = _Selected(src, record, priority, {reason})
            selected[key] = item
        else:
            item.priority = max(item.priority, priority)
            item.reasons.add(reason)
        _add_tool_pair(src, record, selected, priority - 1)
        if window:
            _add_window(src, record.line, window, selected, priority - 2, reason + " context")

    # Always include status-salient records and recent raw tail.
    for src in sources:
        records = _meaningful(src.records)
        for r in records[-RECENT_TAIL_RECORDS:]:
            add(src, r, 40, "recent tail")
        st = src.st
        if st is None:
            continue
        for r in getattr(st, "last_agent_texts", [])[-3:]:
            add(src, r, 55, "latest assistant message")
        for intent in getattr(st, "intents", [])[-3:]:
            add(src, intent, 54, "recent user prompt")
        if getattr(st, "pending_tool", None) is not None:
            add(src, st.pending_tool, 70, "in-flight tool", window=LINE_WINDOW_RADIUS)
        for f in getattr(st, "failures", [])[-4:]:
            add(src, src.by_line.get(f.line), 68, "recent failure", window=LINE_WINDOW_RADIUS)
            add(src, src.by_line.get(f.call_line), 67, "failed tool call")

    # Expand explicit citations or line references from the question/history.
    cited_text = question + "\n" + "\n".join(t for _role, t in history)
    for sid, line in _line_refs(cited_text):
        for src in _matching_sources(sources, sid):
            add(src, src.by_line.get(line), 90, "cited line", window=LINE_WINDOW_RADIUS)

    # Keyword/entity retrieval over the complete observable transcript.
    scored = []
    if terms:
        for src in sources:
            for r in _meaningful(src.records):
                text = _record_text(r)
                score = _match_score(text, terms)
                if score:
                    scored.append((score, r.line, src, r))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    for score, _line, src, r in scored[:KEYWORD_MATCH_RECORDS]:
        add(src, r, 80 + min(score, 9), "keyword match", window=LINE_WINDOW_RADIUS)

    return dict(sorted(selected.items(), key=lambda kv: (
        kv[1].source.label, kv[1].record.line, -kv[1].priority)))


def _meaningful(records: list) -> list:
    return [r for r in records if r.kind in (
        "human", "agent_text", "agent_thinking", "tool_call", "tool_result", "system")]


def _line_refs(text: str) -> list:
    refs = []
    for m in _CITATION.finditer(text or ""):
        refs.append((m.group("sid") or "", int(m.group("line"))))
    for m in _BARE_LINE.finditer(text or ""):
        refs.append(("", int(m.group("line"))))
    out, seen = [], set()
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def _matching_sources(sources: list, sid: str) -> list:
    if not sid:
        return sources[:1]
    sid = sid.lower()
    return [s for s in sources
            if s.session_id.lower().startswith(sid) or s.label.lower().startswith(sid)]


def _match_score(text: str, terms: list) -> int:
    low = (text or "").lower()
    score = 0
    for term in terms:
        count = low.count(term)
        if count:
            score += min(4, count)
    return score


def _add_window(src: _Source, line: int, radius: int, selected: dict,
                priority: int, reason: str) -> None:
    records = _meaningful(src.records)
    idx = next((i for i, r in enumerate(records) if r.line == line), -1)
    if idx < 0:
        return
    lo, hi = max(0, idx - radius), min(len(records), idx + radius + 1)
    for r in records[lo:hi]:
        key = (src.label, r.line, r.kind, r.tool_id)
        item = selected.get(key)
        if item is None:
            selected[key] = _Selected(src, r, priority, {reason})
        else:
            item.priority = max(item.priority, priority)
            item.reasons.add(reason)


def _add_tool_pair(src: _Source, record, selected: dict, priority: int) -> None:
    pair = None
    if record.kind == "tool_call" and record.tool_id:
        pair = src.result_by_id.get(record.tool_id)
    elif record.kind == "tool_result" and record.tool_id:
        pair = src.call_by_id.get(record.tool_id)
    if pair is None:
        return
    key = (src.label, pair.line, pair.kind, pair.tool_id)
    item = selected.get(key)
    if item is None:
        selected[key] = _Selected(src, pair, priority, {"paired tool evidence"})
    else:
        item.priority = max(item.priority, priority)
        item.reasons.add("paired tool evidence")


def _render_raw_records(selected: dict) -> str:
    if not selected:
        return "## Primary Raw Transcript Evidence\n- (no transcript records available)"
    rows = ["## Primary Raw Transcript Evidence",
            "- Raw records below are primary evidence. Summary/index text is orientation only.",
            ""]
    for item in selected.values():
        r = item.record
        reasons = ", ".join(sorted(item.reasons))
        rows.append(f"### {_cite(item.source, r.line)} {_record_label(r)}"
                    + (f" - {reasons}" if reasons else ""))
        rows.append(_clip_record(_record_text(r)))
        rows.append("")
    return "\n".join(rows).rstrip()


def _record_label(r) -> str:
    if r.kind == "human":
        return "human prompt"
    if r.kind == "agent_text":
        return "assistant message"
    if r.kind == "agent_thinking":
        return "assistant thinking snippet"
    if r.kind == "tool_call":
        return f"tool call {r.tool_name or '?'}"
    if r.kind == "tool_result":
        return "tool result" + (" error" if r.is_error else "")
    return r.kind.replace("_", " ")


def _record_text(r) -> str:
    if r.kind in ("human", "agent_text", "agent_thinking", "system"):
        return r.text or ""
    if r.kind == "tool_call":
        payload = json.dumps(r.tool_input or {}, ensure_ascii=False, indent=2, sort_keys=True)
        return f"tool_use_id: {r.tool_id or '?'}\nname: {r.tool_name or '?'}\ninput:\n{payload}"
    if r.kind == "tool_result":
        status = "error" if r.is_error else "ok"
        return f"tool_use_id: {r.tool_id or '?'}\nstatus: {status}\ncontent:\n{r.text or ''}"
    return ""


def _clip_record(text: str) -> str:
    text = text or ""
    if len(text) <= PER_RECORD_CHARS:
        return text
    return (text[:PER_RECORD_CHARS].rstrip()
            + "\n...[record clipped by per-record context budget]")


def _render_chat_history(history: list, max_chars: int) -> str:
    if not history:
        return ""
    pairs = []
    current = []
    for role, text in history:
        current.append((role, text))
        if len(current) == 2:
            pairs.append(current)
            current = []
    if current:
        pairs.append(current)

    chosen = []
    used = 0
    for turn in reversed(pairs):
        block = _turn_block(len(pairs) - len(chosen), turn)
        if not chosen and len(block) > max_chars:
            block = block[-max_chars:].lstrip()
        cost = len(block) + 2
        if chosen and used + cost > max_chars:
            break
        chosen.append(block)
        used += cost
    chosen.reverse()
    omitted = max(0, len(pairs) - len(chosen))
    rows = ["## Cockpit Conversation Context",
            "- Prior cockpit turns are continuity, not fresh observed agent evidence."]
    if omitted:
        rows.append(f"- {omitted} older turn(s) omitted by context budget; raw log remains on disk.")
    rows.append("")
    rows.extend(chosen)
    return "\n".join(rows).rstrip()


def _render_memory(memory_text: str) -> str:
    text = (memory_text or "").strip()
    if not text:
        return ""
    return "## Durable Cockpit Memory\n" + text


def _turn_block(n: int, turn: list) -> str:
    rows = [f"### Cockpit turn {n}"]
    for role, text in turn:
        label = "user" if role == "user" else "cc-copilot"
        rows.append(f"{label}:")
        rows.append(str(text or ""))
    return "\n".join(rows).rstrip()


def _render_project_context(path: str, st, scope: str, enabled: bool, terms: list) -> str:
    if not enabled:
        return ""
    try:
        root = SC._project_root(path, st)
        return _render_budgeted_project_context(root, st, terms)
    except Exception:
        return ""


def _render_budgeted_project_context(root: str, st, terms: list,
                                     max_chars: int = PROJECT_CONTEXT_CHARS) -> str:
    root = os.path.abspath(root or os.getcwd())
    files = SC._text_files(root, max_files=PROJECT_INDEX_FILES)
    by_rel = {rel: p for rel, p in files}
    changed = _changed_project_paths(st, by_rel)
    key_docs = _key_docs(by_rel)
    relevant = _relevant_project_files(files, terms)

    rows = ["## Read-only Project Facts",
            "# cc-copilot project facts — budgeted evidence",
            f"root `{root}`",
            ""]
    used = sum(len(r) for r in rows)

    def add_block(block: list) -> bool:
        nonlocal used
        for row in block:
            cost = len(row) + 1
            if used + cost > max_chars:
                rows.append("- project context budget exhausted before lower-priority tiers  [tree]")
                used = max_chars
                return False
            rows.append(row)
            used += cost
        return True

    if not add_block(_project_git_facts(root)):
        return "\n".join(rows).rstrip()
    if changed and not add_block(_project_path_section("Changed files from session evidence",
                                                       changed, tag="session")):
        return "\n".join(rows).rstrip()

    excerpt_plan = []
    excerpt_plan.extend((rel, "changed file") for rel in changed)
    excerpt_plan.extend((rel, "key doc") for rel in key_docs if rel not in changed)
    excerpt_plan.extend((rel, "question match") for rel in relevant
                        if rel not in changed and rel not in key_docs)

    seen = set()
    if excerpt_plan:
        if not add_block(["## Project file excerpts"]):
            return "\n".join(rows).rstrip()
        for rel, reason in excerpt_plan[:16]:
            if rel in seen:
                continue
            seen.add(rel)
            excerpt = _project_excerpt(by_rel[rel], rel, terms,
                                       reason=reason,
                                       max_lines=PROJECT_EXCERPT_LINES)
            if excerpt and not add_block(excerpt):
                return "\n".join(rows).rstrip()
    elif not add_block(["## Project file excerpts",
                        "- (no changed/key/relevant text file excerpts selected)"]):
        return "\n".join(rows).rstrip()

    index = ["## Broader project file index",
             f"- {len(files)} text file(s) selected for read-only evidence  [tree]"]
    for rel, _p in files[:60]:
        index.append(f"- `{rel}`  [tree]")
    if len(files) > 60:
        index.append(f"- ...and {len(files) - 60} more  [tree]")
    add_block(index)
    return "\n".join(rows).rstrip()


def _project_git_facts(root: str) -> list:
    rows = ["## Git summary"]
    top = _git(root, "rev-parse", "--show-toplevel")
    branch = _git(root, "branch", "--show-current")
    status = _git(root, "status", "--short")
    if top:
        rows.append(f"- repository root `{top.splitlines()[0]}`  [git:root]")
    if branch:
        rows.append(f"- branch `{branch.splitlines()[0] or '(detached)'}`  [git:branch]")
    if status:
        changed = status.splitlines()
        rows.append(f"- working tree has {len(changed)} changed path(s)  [git:status]")
        for line in changed[:20]:
            rows.append(f"  - `{line}`  [git:status]")
        if len(changed) > 20:
            rows.append(f"  - ...and {len(changed) - 20} more  [git:status]")
    else:
        rows.append("- working tree clean or git unavailable  [git:status]")
    rows.append("")
    return rows


def _git(root: str, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _changed_project_paths(st, by_rel: dict) -> list:
    out = []
    for fc in getattr(st, "changed_files", []) or []:
        rel = _normalize_project_rel(fc.path)
        if rel in by_rel and rel not in out:
            out.append(rel)
    return out[:20]


def _normalize_project_rel(path: str) -> str:
    path = (path or "").strip()
    if not path:
        return ""
    return path.replace("\\", "/").lstrip("./")


def _key_docs(by_rel: dict) -> list:
    names = (
        "README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md", "pyproject.toml",
        "package.json", "Cargo.toml", "go.mod", "requirements.txt",
    )
    lower = {rel.lower(): rel for rel in by_rel}
    out = []
    for name in names:
        rel = lower.get(name.lower())
        if rel:
            out.append(rel)
    for rel in sorted(by_rel):
        low = rel.lower()
        base = os.path.basename(low)
        if low.startswith("docs/") and base in ("overview.md", "architecture.md", "readme.md"):
            out.append(rel)
    return out[:12]


def _relevant_project_files(files: list, terms: list) -> list:
    if not terms:
        return []
    scored = []
    for rel, path in files:
        score = _match_score(rel, terms) * 4
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > 400:
                        break
                    score += _match_score(line, terms)
        except OSError:
            continue
        if score:
            scored.append((score, rel))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [rel for _score, rel in scored[:16]]


def _project_path_section(title: str, paths: list, tag: str) -> list:
    rows = [f"## {title}"]
    rows.extend(f"- `{rel}`  [{tag}]" for rel in paths)
    rows.append("")
    return rows


def _project_excerpt(path: str, rel: str, terms: list, reason: str, max_lines: int) -> list:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = [(i, line.rstrip("\n")) for i, line in enumerate(fh, 1)]
    except OSError:
        return []
    if not lines:
        return []
    selected = []
    if terms:
        hits = [n for n, text in lines if _match_score(text, terms)]
        for hit in hits[:12]:
            for n in range(max(1, hit - 2), min(len(lines), hit + 2) + 1):
                if n not in selected:
                    selected.append(n)
                if len(selected) >= max_lines:
                    break
            if len(selected) >= max_lines:
                break
    if not selected:
        selected = [n for n, _text in lines[:max_lines]]
    by_line = dict(lines)
    rows = [f"### `{rel}` — {reason}"]
    for n in selected[:max_lines]:
        rows.append(f"- [{rel}:L{n}] {_clip_project_line(by_line.get(n, ''))}")
    rows.append("")
    return rows


def _clip_project_line(text: str, limit: int = 180) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _render_summary_index(path: str, st, scope: str, selectors: list) -> str:
    try:
        ev = SC.render_evidence(path, st, scope, sessions=selectors, project_context=False)
    except Exception:
        return ""
    return ("## Summary Index (navigation only)\n"
            "Use this to orient retrieval; do not treat shortened recap lines as primary "
            "when raw transcript evidence is available.\n\n" + ev.text)


def _render_status(path: str, st, scope: str, selectors: list, sources: list,
                   terms: list, selected: dict) -> str:
    rows = ["# cc-copilot evidence context",
            "coverage: primary transcript records + read-only project facts; summaries are indexes.",
            f"scope: `{scope}`" + (f" · selected sessions: {', '.join(selectors)}" if selectors else ""),
            f"retrieval: {len(selected)} raw record(s) from {sum(len(s.records) for s in sources)} candidate record(s)"
            + (f" · terms: {', '.join(terms)}" if terms else ""),
            ""]
    if scope != SC.SESSION:
        rows.append("## Current Status Facts")
        if sources:
            labels = ", ".join(f"`{s.label}`" for s in sources)
            rows.append(f"- evidence session(s): {labels}")
            statuses = {}
            for src in sources:
                status = getattr(src.st, "status", "") if src.st is not None else ""
                statuses[status or "unknown"] = statuses.get(status or "unknown", 0) + 1
            rows.append("- session statuses: " + ", ".join(
                f"{name}={count}" for name, count in sorted(statuses.items())))
            roots = sorted({getattr(src.st.tr, "cwd", "") for src in sources
                            if src.st is not None and getattr(src.st.tr, "cwd", "")})
            if roots:
                rows.append(f"- project root `{roots[0]}`")
        else:
            rows.append("- no live evidence sessions available")
        rows.append("")
        return "\n".join(rows).rstrip()
    if st is not None:
        tr = st.tr
        rows.extend([
            "## Current Status Facts",
            f"- anchor session `{(tr.session_id or os.path.basename(path or '')[:-6])[:8]}`"
            f" · status `{st.status}` · {tr.raw_lines} transcript line(s)",
            f"- cwd `{tr.cwd or '?'}`" + (f" · branch `{tr.git_branch}`" if tr.git_branch else ""),
        ])
        if st.idle_seconds is not None:
            rows.append(f"- last activity {_dur(st.idle_seconds)} ago")
        if st.pending_tool is not None:
            p = st.pending_tool
            rows.append(f"- in-flight tool `{p.tool_name or '?'}` at {_cite(_source(path, st), p.line)}")
        rows.append("")
    elif path:
        rows.extend(["## Current Status Facts",
                     f"- anchor transcript `{path}` is unavailable or history-only",
                     ""])
    return "\n".join(rows).rstrip()


def _pack_sections(sections: list, max_chars: int) -> tuple[str, bool]:
    pieces, used, truncated = [], 0, False
    for _name, text in sections:
        if not text:
            continue
        text = text.rstrip()
        cost = len(text) + (2 if pieces else 0)
        if used + cost <= max_chars:
            pieces.append(text)
            used += cost
            continue
        remaining = max_chars - used - (2 if pieces else 0)
        if remaining > 800:
            pieces.append(text[:remaining].rstrip()
                          + "\n...[context budget exhausted; lower-priority evidence omitted]")
            used = max_chars
        truncated = True
        break
    return "\n\n".join(pieces).rstrip(), truncated


def _cite(src: _Source, line: int) -> str:
    return f"[{src.label}:L{line}]" if line else f"[{src.label}]"
