"""Deterministic evidence scopes for chat / narration.

Scopes widen what the LLM may be grounded in without widening what it may do:
all evidence is collected read-only, rendered as text, and cited before it ever
reaches a backend.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from . import assess as A, brief as B, locate as LOC, state as S, sources as SRC
from .brief import _dur, _oneline


SESSION = "session"
MULTI = "multi-session"
PROJECT = "project"
SCOPES = (SESSION, MULTI, PROJECT)

_ALIASES = {
    "1": SESSION, "one": SESSION, "single": SESSION, "session": SESSION,
    "2": MULTI, "multi": MULTI, "multi-session": MULTI, "sessions": MULTI,
    "fleet": MULTI,
    "3": PROJECT, "project": PROJECT, "workspace": PROJECT, "repo": PROJECT,
}

_STATUS_GLYPH = {"running": "RUNNING", "stalled": "STALLED",
                 "awaiting-agent": "AWAITING", "idle": "IDLE", "empty": "EMPTY"}

_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "dist", "build",
    "target", ".next", ".turbo", ".cache", "coverage", ".idea", ".vscode",
}
_SECRET_NAMES = {
    ".env", ".env.local", ".envrc", "id_rsa", "id_dsa", "id_ecdsa",
    "id_ed25519", "credentials.json", "secrets.json",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".sqlite", ".db")
_TEXT_EXTS = {
    ".py", ".md", ".toml", ".txt", ".json", ".yaml", ".yml", ".ini", ".cfg",
    ".sh", ".bash", ".zsh", ".js", ".jsx", ".ts", ".tsx", ".css", ".html",
    ".go", ".rs", ".swift", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
    ".m", ".mm", ".rb", ".php", ".sql", ".xml", ".csv", ".tsv",
}


@dataclass
class EvidenceBrief:
    scope: str
    title: str
    text: str


def normalize(name: str = "") -> str:
    key = (name or SESSION).strip().lower().replace("_", "-")
    if key not in _ALIASES:
        raise ValueError(f"unknown scope {name!r}; expected session, multi-session, or project")
    return _ALIASES[key]


def label(scope: str) -> str:
    return normalize(scope)


def parse_selectors(value) -> list:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        raw = []
        for v in value:
            raw.extend(parse_selectors(v))
        return raw
    return [p.strip() for p in str(value).replace(",", " ").split() if p.strip()]


def render_evidence(path: str, st=None, scope: str = SESSION,
                    sessions=None, project_context: bool = False) -> EvidenceBrief:
    """Render the read-only evidence brief for ``scope``.

    ``path`` is still the cockpit's anchor session. Wider scopes use it to find
    sibling transcripts and the project cwd, but do not mutate anything.
    """
    sc = normalize(scope)
    if st is None and path and os.path.isfile(path):
        st = S.build(SRC.parse(path))
    if sc == SESSION:
        title = _session_title(st, None) if st is not None else "history-only session"
        text = B.render(st) if st is not None else _history_only(path)
        if project_context:
            text += "\n\n" + render_project_facts(_project_root(path, st)) + "\n\n" + _footer(PROJECT)
        return EvidenceBrief(sc, title, text)
    if sc == MULTI:
        text = render_multi_session(path, st, selectors=parse_selectors(sessions))
        if project_context:
            text += "\n\n" + render_project_facts(_project_root(path, st))
            return EvidenceBrief(sc, "multi-session project view", text + "\n\n" + _footer(PROJECT))
        return EvidenceBrief(sc, "multi-session project view", text + "\n\n" + _footer(sc))
    root = _project_root(path, st)
    text = (render_multi_session(path, st, selectors=parse_selectors(sessions))
            + "\n\n" + render_project_facts(root))
    return EvidenceBrief(sc, "project view", text + "\n\n" + _footer(sc))


def exit_code(path: str, st=None, scope: str = SESSION, sessions=None) -> int:
    """Scriptable verdict for a scope: 2 intervene, 1 review, 0 clear-ish."""
    sc = normalize(scope)
    if sc == SESSION:
        verdict = A.assess(st).verdict if st is not None else "empty"
        return {"intervene": 2, "review": 1}.get(verdict, 0)
    worst = 0
    for _ref, _st, a in _session_items(path, st, parse_selectors(sessions)):
        worst = max(worst, {"intervene": 2, "review": 1}.get(a.verdict, 0))
    return worst


def render_multi_session(path: str, current_st=None, max_sessions: int = 12,
                         selectors=None) -> str:
    selectors = parse_selectors(selectors)
    items = _session_items(path, current_st, selectors)
    total = len(_candidate_refs(path)) or len(items)
    cwd = _project_root(path, current_st)
    shown = items[:max_sessions]
    source = f"{len(items)} work-session transcript(s)"
    if selectors:
        source = f"{len(items)} selected of {total} work-session transcript(s)"
    L = [
        f"# cc-copilot multi-session brief — {_oneline(cwd, 80)}",
        f"scope `{MULTI}` · source: {source}"
        + (f" · showing {len(shown)}" if len(shown) != len(items) else ""),
        "",
    ]
    if not shown:
        L += ["## Session board", "- (no work-session transcripts found)"]
        return "\n".join(L)

    L.append("## Session board")
    for ref, st, a in shown:
        sid = _sid(ref, st)
        title = _session_title(st, ref)
        idle = _dur(st.idle_seconds)
        L.append(f"- `{sid}` {_STATUS_GLYPH.get(st.status, st.status)} · safety "
                 f"`{a.verdict}` · idle {idle} · {st.tr.raw_lines} ev · "
                 f"{_oneline(title, 80)}")
        sigs = [s for s in a.signals if s.severity in ("alarm", "warn")]
        if sigs:
            for sig in sigs[:2]:
                L.append(f"  - {sig.severity}: {_oneline(sig.message, 110)}"
                         f"{_sig_cites(sid, sig)}")
        elif st.intents:
            r = st.intents[-1]
            L.append(f"  - latest ask: {_oneline(r.text, 110)}  {_cite(sid, r.line)}")
        elif st.last_record is not None:
            L.append(f"  - tail: {st.last_record.kind}  {_cite(sid, st.last_record.line)}")
    L.append("")

    changes = []
    for ref, st, _a in shown:
        sid = _sid(ref, st)
        for fc in st.changed_files[:4]:
            changes.append((fc.last_line, sid, fc))
    changes.sort(reverse=True, key=lambda x: x[0])
    if changes:
        L.append("## Changed files across sessions")
        for _line, sid, fc in changes[:12]:
            kind = []
            if fc.edits:
                kind.append(f"{fc.edits} edit{'s' if fc.edits != 1 else ''}")
            if fc.writes:
                kind.append(f"{fc.writes} write{'s' if fc.writes != 1 else ''}")
            L.append(f"- `{sid}` `{fc.path}` ({', '.join(kind)})  "
                     f"{_cite(sid, fc.last_line)}")
        L.append("")

    failures = []
    for ref, st, _a in shown:
        sid = _sid(ref, st)
        for f in st.failures[-3:]:
            failures.append((f.line, sid, f))
    failures.sort(reverse=True, key=lambda x: x[0])
    if failures:
        L.append("## Recent friction across sessions")
        for _line, sid, f in failures[:10]:
            L.append(f"- `{sid}` {f.tool} failed: {_oneline(f.summary, 130)}  "
                     f"{_cite(sid, f.line)}")
        L.append("")
    return "\n".join(L).rstrip()


def render_project_facts(root: str, max_files: int = 80,
                         max_chars: int = 50000, per_file_lines: int = 80) -> str:
    root = os.path.abspath(root or os.getcwd())
    L = ["# cc-copilot project facts — read-only workspace evidence",
         f"root `{root}`", ""]
    L.extend(_git_facts(root))
    files = _text_files(root, max_files=max_files)
    L.append("## Project file index")
    L.append(f"- {len(files)} text file(s) selected for read-only evidence  [tree]")
    for rel, _path in files[:40]:
        L.append(f"- `{rel}`  [tree]")
    if len(files) > 40:
        L.append(f"- ...and {len(files) - 40} more  [tree]")
    L.append("")

    L.append("## Project file excerpts")
    used = 0
    included = 0
    for rel, p in files:
        if used >= max_chars:
            break
        excerpt, chars = _excerpt(p, rel, per_file_lines, max_chars - used)
        if not excerpt:
            continue
        included += 1
        used += chars
        L.extend(excerpt)
    if included == 0:
        L.append("- (no readable project files within the evidence budget)")
    else:
        L.append(f"- excerpt budget used: {used} characters across {included} file(s)  [tree]")
    return "\n".join(L).rstrip()


def resolve_session_refs(path: str, selectors=None) -> list:
    """Resolve session selectors to refs in this transcript directory.

    Selectors may be list numbers from the session list, full ids, id prefixes,
    or transcript paths. Empty selectors mean all candidate work sessions.
    """
    refs = _candidate_refs(path)
    selectors = parse_selectors(selectors)
    if not selectors:
        return refs
    out = []
    seen = set()
    missing = []
    for sel in selectors:
        match = None
        if sel in ("all", "*"):
            return refs
        if sel.isdigit():
            i = int(sel) - 1
            if 0 <= i < len(refs):
                match = refs[i]
        elif os.path.isfile(sel):
            target = os.path.abspath(sel)
            for r in refs:
                if os.path.abspath(r.path) == target:
                    match = r
                    break
        else:
            exact = [r for r in refs if r.session_id == sel]
            pref = [r for r in refs if r.session_id.startswith(sel)]
            matches = exact or pref
            if len(matches) == 1:
                match = matches[0]
            elif len(matches) > 1:
                missing.append(f"{sel!r} is ambiguous")
                continue
        if match is None:
            missing.append(repr(sel))
            continue
        key = os.path.abspath(match.path)
        if key not in seen:
            seen.add(key)
            out.append(match)
    if missing:
        raise ValueError("no session matching " + ", ".join(missing))
    return out


def _candidate_refs(path: str) -> list:
    """All work sessions for the anchor's project, across in-scope agents.

    The anchor agent's own sessions are discovered the way that agent groups a
    project — Claude Code co-locates them in one directory (robust even outside
    the canonical projects dir); Codex scatters them by date and is found by
    cwd. Other in-scope agents are unioned in by project cwd, so a multi-session
    view spans Claude Code and Codex for the same project.
    """
    here = os.path.abspath(path) if path else ""
    src = SRC.source_for_path(path) if path else None
    anchor_agent = src.name if src is not None else "claude"

    refs = []
    if anchor_agent == "claude":
        refs = LOC.refs_in_dir(os.path.dirname(path), include_own=True) if path else []
    cwd = SRC.read_cwd(path) if path else ""
    if cwd:
        for ref in SRC.list_sessions(cwd, include_own=True):
            if ref.agent == "claude" and anchor_agent == "claude":
                continue  # already covered by co-located siblings
            refs.append(ref)

    refs = [r for r in refs if not r.own or os.path.abspath(r.path) == here]
    if path and os.path.isfile(path) and not any(os.path.abspath(r.path) == here for r in refs):
        try:
            st = S.build(SRC.parse(path))
            sid = getattr(st.tr, "session_id", "") or os.path.basename(path)[:-6]
            refs.append(LOC.SessionRef(
                path, sid, os.path.getmtime(path), os.path.getsize(path),
                getattr(st.tr, "title", "") or "", False, agent=anchor_agent))
        except OSError:
            pass

    seen, deduped = set(), []
    for r in sorted(refs, key=lambda r: r.mtime, reverse=True):
        k = os.path.abspath(r.path)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)
    _mark_current_session(deduped, here)
    return deduped


def _mark_current_session(refs: list, here: str) -> None:
    """Ensure the human's *own* live session is always selectable from a picker.

    cc-copilot supervises *another* agent by default, but the session you're
    sitting in may be in a different project than the one being watched — so it
    would never appear in the project-scoped candidate list. Tag it ``live`` if
    present, and inject it (cross-project) if not, so ``/sessions`` can always
    offer "observe my current session".
    """
    cur_id = LOC.current_session_id()
    if not cur_id:
        return
    for r in refs:
        if r.session_id == cur_id or r.session_id.startswith(cur_id):
            r.live = True
            return
    cur_path = LOC.current_session_path()
    if not cur_path or os.path.abspath(cur_path) == here:
        return
    try:
        st = S.build(SRC.parse(cur_path))
        title = getattr(st.tr, "title", "") or ""
    except OSError:
        title = ""
    try:
        ref = LOC.SessionRef(
            cur_path, cur_id, os.path.getmtime(cur_path), os.path.getsize(cur_path),
            title, False, agent=SRC.source_for_path(cur_path).name, live=True)
    except OSError:
        return
    refs.append(ref)


def _session_items(path: str, current_st=None, selectors=None) -> list:
    refs = resolve_session_refs(path, selectors)
    here = os.path.abspath(path) if path else ""
    out = []
    for ref in refs:
        try:
            st = current_st if os.path.abspath(ref.path) == here and current_st is not None \
                else S.build(SRC.parse(ref.path))
            a = A.assess(st)
        except Exception:
            continue
        out.append((ref, st, a))
    out.sort(key=lambda x: (_rank(x[1].status, x[2].verdict),
                            x[1].idle_seconds if x[1].idle_seconds is not None else 9e9,
                            -x[0].mtime))
    return out


def _rank(status: str, verdict: str) -> int:
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


def _project_root(path: str, st=None) -> str:
    tr = getattr(st, "tr", None)
    cwd = (getattr(tr, "cwd", "") if tr is not None else "") or SRC.read_cwd(path or "")
    return os.path.abspath(cwd or os.getcwd())


def _sid(ref, st) -> str:
    return ((getattr(st.tr, "session_id", "") or getattr(ref, "session_id", ""))[:8]
            or "session")


def _session_title(st, ref) -> str:
    return (getattr(st.tr, "title", "") or getattr(ref, "title", "")
            or (st.intents[-1].text if getattr(st, "intents", None) else "")
            or "(untitled)")


def _cite(sid: str, line: int) -> str:
    return f"[{sid}:L{line}]" if line else ""


def _sig_cites(sid: str, sig) -> str:
    return ("  " + " ".join(_cite(sid, n) for n in sig.evidence)) if sig.evidence else ""


def _history_only(path: str) -> str:
    return ("# cc-copilot brief — history-only\n"
            f"transcript `{path or '?'}` is unavailable; this scope has no live evidence.")


def _footer(scope: str) -> str:
    if scope == MULTI:
        return ("_Every `[session:L…]` citation points to a JSONL line in that "
                "session transcript. cc-copilot states nothing it can't cite._")
    return ("_Project scope is read-only. `[session:L…]` citations point to "
            "transcript lines; `[path:L…]` citations point to project file lines; "
            "`[tree]`/`[git:*]` citations come from deterministic local reads._")


def _git_facts(root: str) -> list:
    L = ["## Git status"]
    top = _git(root, "rev-parse", "--show-toplevel")
    branch = _git(root, "branch", "--show-current")
    status = _git(root, "status", "--short")
    if top:
        L.append(f"- repository root `{top.splitlines()[0]}`  [git:root]")
    if branch:
        L.append(f"- branch `{branch.splitlines()[0] or '(detached)'}`  [git:branch]")
    if status:
        rows = status.splitlines()
        L.append(f"- working tree has {len(rows)} changed path(s)  [git:status]")
        for row in rows[:20]:
            L.append(f"  - `{row}`  [git:status]")
        if len(rows) > 20:
            L.append(f"  - ...and {len(rows) - 20} more  [git:status]")
    else:
        L.append("- working tree clean or git unavailable  [git:status]")
    L.append("")
    return L


def _git(root: str, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _text_files(root: str, max_files: int) -> list:
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".cache"))
        for name in sorted(files):
            p = os.path.join(base, name)
            rel = os.path.relpath(p, root)
            if _skip_file(name, rel):
                continue
            if _looks_text(p):
                out.append((rel, p))
                if len(out) >= max_files:
                    return out
    return out


def _skip_file(name: str, rel: str) -> bool:
    low = name.lower()
    if low in _SECRET_NAMES or low.startswith(".env."):
        return True
    if low.endswith(_SECRET_SUFFIXES):
        return True
    if rel.startswith(".git" + os.sep):
        return True
    return False


def _looks_text(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in _TEXT_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(2048)
    except OSError:
        return False
    return b"\0" not in chunk


def _excerpt(path: str, rel: str, max_lines: int, budget: int) -> tuple:
    if budget <= 0:
        return [], 0
    L = [f"### `{rel}`"]
    used = len(L[0])
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                if n > max_lines or used >= budget:
                    break
                text = line.rstrip("\n")
                row = f"- [{rel}:L{n}] {_oneline(text, 180)}"
                L.append(row)
                used += len(row)
    except OSError:
        return [], 0
    if len(L) == 1:
        return [], 0
    L.append("")
    return L, used
