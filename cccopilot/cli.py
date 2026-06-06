"""cc-copilot command-line interface.

    cc-copilot sessions               list this project's sessions (newest first)
    cc-copilot brief [--latest|--session ID|PATH]   evidence-cited recap
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

from . import __version__, locate, transcript as T, state as S, brief as B


def _resolve_or_die(args) -> str:
    cwd = args.cwd or os.getcwd()
    path = locate.resolve(cwd, getattr(args, "session", None))
    if not path:
        sys.stderr.write(
            f"cc-copilot: no Claude Code session found for {cwd!r}\n"
            f"  looked in: {locate.project_dir_for(cwd) or locate.projects_root()}\n"
            f"  try: cc-copilot sessions --cwd <project-dir>\n")
        sys.exit(2)
    return path


def cmd_sessions(args) -> int:
    cwd = args.cwd or os.getcwd()
    refs = locate.list_sessions(cwd)
    if not refs:
        print(f"(no sessions for {cwd})  dir: {locate.project_dir_for(cwd)}")
        return 1
    print(f"sessions for {cwd}  ({len(refs)}):")
    for r in refs:
        kb = r.size / 1024
        print(f"  {r.session_id}  {r.hhmm}  {kb:7.0f} KB")
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
    print(B.render(st))
    if getattr(args, "narrate", False):
        from . import narrate as N
        if not N.available():
            sys.stderr.write("# --narrate: no LLM backend (install `claude` CLI "
                             "or set CC_COPILOT_LLM_CMD); showing brief only\n")
        else:
            sys.stderr.write(f"# narrating via {N.backend_name()} …\n")
            try:
                txt = N.narrate(st, model=getattr(args, "model", None))
                print("\n## 🗣 Narration  _(LLM, grounded in the cited facts above)_\n")
                print(txt)
            except Exception as e:
                sys.stderr.write(f"# narration failed: {e}\n")
    return 0


def cmd_ask(args) -> int:
    _, tr, st = _load(args)
    from . import narrate as N
    if not N.available():
        sys.stderr.write("cc-copilot: `ask` needs an LLM backend (install the "
                         "`claude` CLI or set CC_COPILOT_LLM_CMD)\n")
        return 2
    sys.stderr.write(f"# {N.backend_name()} (grounded in the session state) …\n")
    try:
        print(N.ask(st, args.question, model=getattr(args, "model", None)))
    except Exception as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 1
    return 0


def cmd_check(args) -> int:
    path, tr, st = _load(args)
    if args.path:
        sys.stderr.write(f"# transcript: {path}\n")
    print(B.render_check(st))
    # exit code encodes the verdict, so it's scriptable in a hook/CI:
    #   0 clear/idle/awaiting/empty · 1 review · 2 intervene
    from .assess import assess
    v = assess(st).verdict
    return {"intervene": 2, "review": 1}.get(v, 0)


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
    sp.set_defaults(func=cmd_sessions)

    def session_args(sp):
        common(sp)
        sp.add_argument("session", nargs="?",
                        help="session id, id-prefix, or transcript path "
                             "(default: most recent, excluding the current session)")
        sp.add_argument("--latest", action="store_true",
                        help="explicitly target the most recent session")
        sp.add_argument("--path", action="store_true",
                        help="also print the resolved transcript path to stderr")

    sp = sub.add_parser("brief", help="evidence-cited recap of a session")
    session_args(sp)
    sp.add_argument("--narrate", action="store_true",
                    help="append an LLM narration grounded in the cited facts")
    sp.add_argument("--model", help="model for --narrate (passed to the backend)")
    sp.set_defaults(func=cmd_brief)

    sp = sub.add_parser("check", help="is it safe to continue? (off-track/friction signals)")
    session_args(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("ask", help="ask a question grounded in the session state")
    common(sp)
    sp.add_argument("question", help='e.g. "did it drift?" or "draft the next instruction"')
    sp.add_argument("--session", help="session id, prefix, or path (default: most recent)")
    sp.add_argument("--model", help="model passed to the LLM backend")
    sp.set_defaults(func=cmd_ask, path=False)

    sp = sub.add_parser("state", help="dump the raw state model as JSON")
    session_args(sp)
    sp.add_argument("--json", action="store_true", help="(default output is JSON)")
    sp.set_defaults(func=cmd_state)

    sp = sub.add_parser("watch", help="re-render the brief as the transcript grows")
    session_args(sp)
    sp.add_argument("--interval", type=int, default=5, help="poll seconds (default 5)")
    sp.set_defaults(func=cmd_watch)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "latest", False):
        args.session = None
    return args.func(args)
