"""cc-copilot command-line interface.

    cc-copilot sessions               list this project's sessions (newest first)
    cc-copilot brief [--latest|--session ID|PATH]   evidence-cited recap
    cc-copilot chat [...]             live read-only chat pinned to a session
    cc-copilot backends               list LLM backends (claude/codex/deepseek/…)
    cc-copilot config [--init]        default backend/model/keys (~/.cc-copilot.toml)
    cc-copilot watch [...]            re-print the brief when the transcript grows
    cc-copilot state [...] --json     dump the raw state model as JSON

Read-only by design: it never writes to the transcript or touches the agent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict

from . import __version__, locate, transcript as T, state as S, brief as B, scope as SC


def _repo_root() -> str:
    """The dir holding the cccopilot package (where .venv should live)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tui_importable() -> bool:
    import importlib.util
    return importlib.util.find_spec("textual") is not None


def _ensure_tui_runtime(quiet: bool = False) -> str:
    """Return a python that has Textual — the project .venv, creating it and
    installing the [tui] extra on first use. Returns None on failure."""
    import subprocess
    import venv
    vdir = os.path.join(_repo_root(), ".venv")
    vpy = os.path.join(vdir, "bin", "python")

    def has_textual(py):
        return subprocess.run([py, "-c", "import textual"],
                              capture_output=True).returncode == 0

    if os.path.isfile(vpy) and has_textual(vpy):
        return vpy
    if not os.path.isfile(vpy):
        if not quiet:
            sys.stderr.write(f"# cc-copilot: creating venv at {vdir} (one-time) …\n")
        try:
            venv.create(vdir, with_pip=True)
        except Exception as e:
            sys.stderr.write(f"# venv creation failed: {e}\n")
            return None
    if not quiet:
        sys.stderr.write("# cc-copilot: installing the cockpit (textual), one-time …\n")
    r = subprocess.run([vpy, "-m", "pip", "install", "-q", "--upgrade", "textual"])
    if r.returncode != 0 or not has_textual(vpy):
        return None
    return vpy


def cmd_setup(args) -> int:
    import subprocess
    vpy = _ensure_tui_runtime()
    if not vpy:
        sys.stderr.write("cc-copilot setup: could not install textual.\n"
                         "Try manually:  python3 -m venv .venv && .venv/bin/pip install textual\n")
        return 1
    subprocess.run([vpy, "-c",
                    "import textual; print('cockpit ready · textual', textual.__version__)"])
    print("run:  cc-copilot cockpit")
    return 0


def _resolve_or_die(args) -> str:
    cwd = args.cwd or os.getcwd()
    latest = bool(getattr(args, "latest", False))
    session = None if latest else getattr(args, "session", None)
    path = locate.resolve(cwd, session, include_current=latest)
    if not path:
        cmd = getattr(args, "cmd", None) or "cockpit"
        sys.stderr.write(f"cc-copilot: no agent session in {cwd!r} "
                         f"(it watches another project's agent, not this dir).\n")
        projs = locate.projects_with_sessions()
        if projs:
            sys.stderr.write("  recent sessions are in:\n")
            for p, n, mt in projs[:6]:
                sys.stderr.write(f"    {(p or '(unknown)'):<42} {n} session"
                                 f"{'s' if n != 1 else ''} · {locate.ago(mt)} ago\n")
            sys.stderr.write(f"  → cc-copilot {cmd} --cwd {projs[0][0] or '<project-dir>'}\n")
        else:
            sys.stderr.write(f"  looked in: {locate.project_dir_for(cwd) or locate.projects_root()}\n"
                             f"  try: cc-copilot sessions --cwd <project-dir>\n")
        sys.exit(2)
    return path


def cmd_sessions(args) -> int:
    cwd = args.cwd or os.getcwd()
    all_refs = locate.list_sessions(cwd, include_own=True)
    refs = [r for r in all_refs if r.own] if getattr(args, "helpers", False) \
        else [r for r in all_refs if not r.own]
    hidden = len([r for r in all_refs if r.own]) if not getattr(args, "helpers", False) else 0
    hnote = f"  [{hidden} cc-copilot helper session(s) hidden; --helpers to show]" if hidden else ""
    if not refs:
        print(f"(no work sessions for {cwd}){hnote}\n  dir: {locate.project_dir_for(cwd)}")
        return 1
    print(f"sessions for {cwd}  ({len(refs)}){hnote}:")
    for r in refs:
        title = f"  {r.title}" if r.title else ""
        print(f"  {r.session_id}  {r.hhmm}  {r.size / 1024:7.0f} KB{title}")
    return 0


def cmd_history(args) -> int:
    from . import store as ST
    if not ST.enabled():
        print("history is disabled (set [history] enabled = true in ~/.cc-copilot.toml, "
              "or unset CC_COPILOT_HISTORY)")
        return 1
    cwd = None if getattr(args, "all", False) else (args.cwd or os.getcwd())
    headers = ST.list_conversations(cwd)
    if not headers:
        scope = "any project" if cwd is None else cwd
        print(f"(no saved copilot conversations for {scope})\n  history dir: {ST.state_home()}")
        return 1
    scope = "all projects" if cwd is None else cwd
    print(f"saved copilot conversations — {scope}  ({len(headers)}):")
    for h in headers:
        gone = "  (transcript gone)" if not h.transcript_present else ""
        proj = os.path.basename(h.cwd) or "?"
        print(f"  {h.conv_id[:8]}  {locate.ago(h.updated):>5} ago  {h.turns:>3}t  "
              f"{(h.title or '(untitled)')[:40]:<40}  {proj}{gone}")
    return 0


def _load(args):
    path = _resolve_or_die(args)
    tr = T.parse(path)
    st = S.build(tr)
    return path, tr, st


def cmd_brief(args) -> int:
    path, tr, st = _load(args)
    if args.path:
        sys.stderr.write(f"# transcript: {path}\n")
    try:
        ev = SC.render_evidence(path, st, getattr(args, "scope", SC.SESSION),
                                sessions=getattr(args, "scope_sessions", ""))
    except ValueError as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 2
    print(ev.text)
    if getattr(args, "narrate", False):
        from . import narrate as N
        be = getattr(args, "backend", None)
        if not N.available(be):
            sys.stderr.write(f"# --narrate: backend unavailable ({N.backend_name(be)}); "
                             f"showing brief only — see `cc-copilot backends`\n")
        else:
            sys.stderr.write(f"# narrating via {N.backend_name(be)} …\n")
            try:
                txt = N.narrate_brief(ev.text, model=getattr(args, "model", None), backend=be)
                print("\n## 🗣 Narration  _(LLM, grounded in the cited facts above)_\n")
                print(txt)
            except Exception as e:
                sys.stderr.write(f"# narration failed: {e}\n")
    return 0


def cmd_ask(args) -> int:
    path, tr, st = _load(args)
    from . import narrate as N
    be = getattr(args, "backend", None)
    if not N.available(be):
        sys.stderr.write(f"cc-copilot: backend unavailable ({N.backend_name(be)}). "
                         f"Run `cc-copilot backends` to see options.\n")
        return 2
    try:
        ev = SC.render_evidence(path, st, getattr(args, "scope", SC.SESSION),
                                sessions=getattr(args, "scope_sessions", ""))
    except ValueError as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 2
    sys.stderr.write(f"# {N.backend_name(be)} (grounded in {ev.scope} evidence) …\n")
    try:
        print(N.ask_brief(ev.text, args.question, model=getattr(args, "model", None), backend=be))
    except Exception as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 1
    return 0


def cmd_backends(args) -> int:
    from . import backends as BK
    from . import narrate as N
    active = BK.resolve(getattr(args, "backend", None)).name
    print("LLM backends (default selection marked ▶; the deterministic core needs none):")
    for name, be in sorted(BK.registry().items()):
        ok = be.available()
        mark = "▶" if name == active else " "
        status = "ready" if ok else f"needs: {be.reason()}"
        print(f"  {mark} {name:<11} {'✓' if ok else '·'} {status}")
    print(f"\nactive: {N.backend_name(getattr(args, 'backend', None))}")
    print("pick with --backend <name>, env CC_COPILOT_BACKEND, or a custom "
          "CC_COPILOT_LLM_CMD / CC_COPILOT_API_BASE.")
    return 0


def _fleet_rank(status, verdict):
    """Sort key so the sessions that need you float to the top."""
    if status == "stalled" or verdict == "intervene":
        return 0
    if status == "awaiting-agent":
        return 1
    if status == "running":
        return 2 if verdict == "review" else 3
    if verdict == "review":
        return 4            # idle, but had unresolved friction
    if status == "idle":
        return 5
    return 6                # empty


def cmd_status(args) -> int:
    from .assess import assess
    from .chat import _GLYPH, _dur
    cwd = args.cwd or os.getcwd()
    all_refs = locate.list_sessions(cwd, include_own=True)
    refs = [r for r in all_refs if not r.own]
    hidden = len(all_refs) - len(refs)
    if not refs:
        note = f"  ({hidden} cc-copilot helper session(s) hidden)" if hidden else ""
        print(f"(no work sessions for {cwd}){note}\n  dir: {locate.project_dir_for(cwd)}")
        return 1
    chosen = refs if getattr(args, "all", False) else refs[:args.limit]
    rows = []
    for r in chosen:
        tr = T.parse(r.path)
        st = S.build(tr)
        a = assess(st)
        sigs = [s for s in a.signals if s.severity in ("alarm", "warn")]
        if sigs:
            head = sigs[0].message + (f" [L{sigs[0].evidence[0]}]" if sigs[0].evidence else "")
        elif st.intents:
            head = st.intents[-1].text
        else:
            head = tr.title or ""
        rows.append((r, st, a, head))
    rows.sort(key=lambda x: (_fleet_rank(x[1].status, x[2].verdict),
                             x[1].idle_seconds if x[1].idle_seconds is not None else 9e9))
    hnote = f", {hidden} helper hidden" if hidden else ""
    print(f"cc-copilot status — {cwd}  ({len(chosen)} of {len(refs)} sessions{hnote})")
    for r, st, a, head in rows:
        g = _GLYPH.get(st.status, "?")
        idle = _dur(st.idle_seconds)
        clip = " ".join((head or "").split())[:56]
        print(f" {g} {st.status:<13} {a.verdict:<9} {idle:>6} ago  {st.tr.raw_lines:>5}ev  "
              f"{r.session_id[:8]}  {clip}")
    return 0


def cmd_check(args) -> int:
    path, tr, st = _load(args)
    if args.path:
        sys.stderr.write(f"# transcript: {path}\n")
    sc = SC.normalize(getattr(args, "scope", SC.SESSION))
    selectors = getattr(args, "scope_sessions", "")
    try:
        print(B.render_check(st) if sc == SC.SESSION
              else SC.render_evidence(path, st, sc, sessions=selectors).text)
        rc = SC.exit_code(path, st, sc, sessions=selectors)
    except ValueError as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 2
    # exit code encodes the verdict, so it's scriptable in a hook/CI:
    #   0 clear/idle/awaiting/empty · 1 review · 2 intervene
    return rc


def cmd_chat(args) -> int:
    from . import chat as C
    # Cockpit requested but Textual isn't in THIS interpreter → bootstrap the
    # .venv once and re-exec under it, so `cc-copilot cockpit` just works.
    if getattr(args, "tui", False) and not _tui_importable():
        vpy = _ensure_tui_runtime()
        if not vpy:
            sys.stderr.write("could not set up the cockpit. Try: cc-copilot setup\n")
            return 3
        if os.path.abspath(vpy) != os.path.abspath(sys.executable):
            os.execve(vpy, [vpy, "-m", "cccopilot", *sys.argv[1:]],
                      {**os.environ, "PYTHONPATH": _repo_root(), "PYTHONSAFEPATH": "1"})
            # execve replaces this process; nothing below runs

    path = _resolve_or_die(args)
    try:
        session = C.ChatSession(
            path,
            model=getattr(args, "model", None),
            backend=getattr(args, "backend", None),
            alerts=not getattr(args, "no_alerts", False),
            poll=getattr(args, "poll", 5),
            persist=getattr(args, "persist", None) is not False,  # None/True persist; False off
            scope=getattr(args, "scope", SC.SESSION),
            scope_sessions=getattr(args, "scope_sessions", ""),
        )
    except ValueError as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 2
    if getattr(args, "tui", False):
        from . import tui
        tui.run(session, poll=getattr(args, "poll", 5),
                alerts=not getattr(args, "no_alerts", False))
        return 0
    session.loop()
    return 0


def cmd_state(args) -> int:
    from .assess import assess
    _, tr, st = _load(args)
    a = assess(st)
    out = {
        "assessment": {
            "verdict": a.verdict,
            "headline": a.headline,
            "signals": [
                {"kind": s.kind, "severity": s.severity,
                 "message": s.message, "evidence": s.evidence}
                for s in a.signals
            ],
        },
        "session_id": tr.session_id,
        "cwd": tr.cwd,
        "git_branch": tr.git_branch,
        "version": tr.version,
        "permission_mode": tr.permission_mode,
        "events": tr.raw_lines,
        "status": st.status,
        "idle_seconds": st.idle_seconds,
        "duration_seconds": st.duration_seconds,
        "tool_counts": st.tool_counts,
        "intents": [{"line": r.line, "ts": r.raw_ts, "text": r.text} for r in st.intents],
        "todos": st.todos,
        "changed_files": [asdict(c) for c in st.changed_files],
        "commands": [asdict(c) for c in st.commands],
        "failures": [asdict(f) for f in st.failures],
        "pending_tool": (
            {"line": st.pending_tool.line, "tool": st.pending_tool.tool_name}
            if st.pending_tool else None
        ),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_watch(args) -> int:
    path = _resolve_or_die(args)
    interval = max(2, args.interval)
    last_size = -1
    sys.stderr.write(f"# watching {path} (every {interval}s, Ctrl-C to stop)\n")
    try:
        while True:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            if size != last_size:
                last_size = size
                tr = T.parse(path)
                st = S.build(tr)
                os.system("clear" if os.name != "nt" else "cls")
                print(B.render(st))
                print(f"\n_(watching · {time.strftime('%H:%M:%S')} · {tr.raw_lines} events)_")
            time.sleep(interval)
    except KeyboardInterrupt:
        sys.stderr.write("\n# stopped\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cc-copilot", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"cc-copilot {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--cwd", help="project dir to resolve sessions for (default: $PWD)")

    sp = sub.add_parser("sessions", help="list sessions for this project")
    common(sp)
    sp.add_argument("--helpers", action="store_true",
                    help="show cc-copilot's own narration sessions instead of hiding them")
    sp.set_defaults(func=cmd_sessions)

    sp = sub.add_parser("history", help="list saved copilot conversations (newest first)")
    common(sp)
    sp.add_argument("--all", action="store_true",
                    help="every project's conversations, not just this cwd's")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("status", aliases=["fleet"],
                        help="overview of ALL sessions in a project (status + safety), neediest first")
    common(sp)
    sp.add_argument("--limit", type=int, default=10, help="how many recent sessions (default 10)")
    sp.add_argument("--all", action="store_true", help="every session, not just the recent --limit")
    sp.set_defaults(func=cmd_status)

    def session_args(sp):
        common(sp)
        sp.add_argument("session", nargs="?",
                        help="session id, id-prefix, or transcript path "
                             "(default: most recent, excluding the current session)")
        sp.add_argument("--latest", action="store_true",
                        help="explicitly target the most recent session")
        sp.add_argument("--path", action="store_true",
                        help="also print the resolved transcript path to stderr")

    def scope_arg(sp):
        sp.add_argument("--scope", type=SC.normalize, default=SC.SESSION,
                        help="grounding scope: session, multi-session, or project "
                             "(aliases: multi, repo)")
        sp.add_argument("--scope-sessions", default="",
                        help="comma-separated (or quoted space-separated) session numbers, ids, "
                             "prefixes, or paths for multi-session/project scope (default: all)")

    sp = sub.add_parser("brief", help="evidence-cited recap of a session")
    session_args(sp)
    scope_arg(sp)
    sp.add_argument("--narrate", action="store_true",
                    help="append an LLM narration grounded in the cited facts")
    sp.add_argument("--model", help="model for --narrate (passed to the backend)")
    sp.add_argument("--backend", help="LLM backend (claude/codex/deepseek/ollama/…; see `backends`)")
    sp.set_defaults(func=cmd_brief)

    sp = sub.add_parser("backends", help="list LLM backends and their availability")
    sp.add_argument("--backend", help="show this backend as the active selection")
    sp.set_defaults(func=cmd_backends)

    sp = sub.add_parser("config",
                        help="show or scaffold ~/.cc-copilot.toml (default backend/model/keys)")
    sp.add_argument("--init", action="store_true",
                    help="write a starter config file if none exists")
    sp.set_defaults(func=cmd_config)

    sp = sub.add_parser("setup",
                        help="install the cockpit TUI extra (creates .venv + textual)")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("check", help="is it safe to continue? (off-track/friction signals)")
    session_args(sp)
    scope_arg(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("ask", help="ask a question grounded in the session state")
    common(sp)
    sp.add_argument("question", help='e.g. "did it drift?" or "draft the next instruction"')
    sp.add_argument("--session", help="session id, prefix, or path (default: most recent)")
    sp.add_argument("--model", help="model passed to the LLM backend")
    sp.add_argument("--backend", help="LLM backend (claude/codex/deepseek/ollama/…)")
    scope_arg(sp)
    sp.set_defaults(func=cmd_ask, path=False)

    def add_chat_args(sp):
        common(sp)
        sp.add_argument("session", nargs="?",
                        help="session id, prefix, or path (default: most recent OTHER session)")
        sp.add_argument("--model", help="model passed to the LLM backend")
        sp.add_argument("--backend", help="LLM backend (codex/claude/deepseek/ollama/…)")
        sp.add_argument("--no-alerts", action="store_true",
                        help="disable the background stall/off-track alert thread")
        sp.add_argument("--no-persist", dest="persist", action="store_false", default=None,
                        help="don't save/restore this chat's history (in-memory only)")
        sp.add_argument("--poll", type=int, default=5,
                        help="alert poll interval in seconds (default 5)")
        scope_arg(sp)

    sp = sub.add_parser("chat", aliases=["attach"],
                        help="interactive read-only chat pinned to a session's live timeline")
    add_chat_args(sp)
    sp.add_argument("--tui", action="store_true",
                    help="full-screen cockpit TUI (needs the cc-copilot[tui] extra)")
    sp.set_defaults(func=cmd_chat, path=False)

    sp = sub.add_parser("cockpit",
                        help="full-screen TUI cockpit (= chat --tui; needs cc-copilot[tui])")
    add_chat_args(sp)
    sp.set_defaults(func=cmd_chat, path=False, tui=True)

    sp = sub.add_parser("state", help="dump the raw state model as JSON")
    session_args(sp)
    sp.add_argument("--json", action="store_true", help="(default output is JSON)")
    sp.set_defaults(func=cmd_state)

    sp = sub.add_parser("watch", help="re-render the brief as the transcript grows")
    session_args(sp)
    sp.add_argument("--interval", type=int, default=5, help="poll seconds (default 5)")
    sp.set_defaults(func=cmd_watch)

    return p


def cmd_config(args) -> int:
    from . import config as CFG, narrate as N
    if getattr(args, "init", False):
        print(CFG.init_file())
        return 0
    p = CFG.path()
    exists = os.path.isfile(p)
    print(f"config: {p}")
    print(f"  status: {'loaded' if exists else 'not present — run `cc-copilot config --init`'}")
    print(f"  effective backend: {N.backend_name()}")
    return 0


def main(argv=None) -> int:
    from . import config as CFG
    args = build_parser().parse_args(argv)
    CFG.apply_defaults(args)   # config file fills gaps the flags/env left
    return args.func(args)
