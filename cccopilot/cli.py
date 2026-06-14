"""cc-copilot command-line interface.

    cc-copilot                        no arguments: open the cockpit
    cc-copilot launch [-- AGENT …]    start an agent + the cockpit side by side (tmux)
    cc-copilot init                   first-run setup: pick the model, save config
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

from . import (__version__, locate, sources as SRC, state as S, brief as B,
               scope as SC, observe as O, context as EC)


def _repo_root() -> str:
    """The dir holding the cccopilot package (where .venv should live)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tui_importable() -> bool:
    import importlib.util
    return importlib.util.find_spec("textual") is not None


def _is_source_checkout() -> bool:
    """True only when running from a git clone — the lone place the auto-`.venv`
    bootstrap belongs. An installed wheel lives in site-packages with no
    pyproject/.git beside it, and that dir may be read-only (uv/pipx tool
    installs), so we must never write a `.venv` there."""
    root = _repo_root()
    return (os.path.isfile(os.path.join(root, "pyproject.toml"))
            or os.path.isdir(os.path.join(root, ".git")))


def _install_extra_hint() -> str:
    return (
        "cc-copilot: the cockpit needs the optional TUI extra (Textual).\n"
        "Reinstall cc-copilot with it:\n"
        "  uv tool install \"cc-copilot[tui]\"       # or: pipx install \"cc-copilot[tui]\"\n"
        "  # plain pip:  python3 -m pip install --user \"cc-copilot[tui]\"\n"
    )


def _setup_troubleshooting() -> str:
    return (
        "cc-copilot setup: could not install Textual.\n"
        "\n"
        "On Debian/Ubuntu minimal Python installs, venv support is often split out:\n"
        "  sudo apt-get update && sudo apt-get install -y python3-venv python3-pip\n"
        "  cc-copilot setup\n"
        "\n"
        "If you cannot use sudo, install Textual into your current/user Python instead:\n"
        "  python3 -m pip install --user 'textual>=2.0'\n"
        "\n"
        "Or (re)install cc-copilot with the TUI extra:\n"
        "  uv tool install \"cc-copilot[tui]\"   #  or: pipx install \"cc-copilot[tui]\"\n"
    )


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

    if _tui_importable():
        return sys.executable
    if os.path.isfile(vpy) and has_textual(vpy):
        return vpy
    if not _is_source_checkout():
        # installed via pip/uv/pipx, not a clone: never build a .venv inside
        # site-packages (it pollutes a writable tool env and fails a read-only
        # one). Point the user at the optional extra instead.
        if not quiet:
            sys.stderr.write(_install_extra_hint())
        return None
    if not os.path.isfile(vpy):
        if not quiet:
            sys.stderr.write(f"# cc-copilot: creating venv at {vdir} (one-time) …\n")
        try:
            venv.create(vdir, with_pip=True)
        except Exception as e:
            sys.stderr.write(f"# venv creation failed: {e}\n")
            if not quiet:
                sys.stderr.write(_setup_troubleshooting())
            return None
    if not quiet:
        sys.stderr.write("# cc-copilot: installing the cockpit (textual), one-time …\n")
    r = subprocess.run([vpy, "-m", "pip", "install", "-q", "--upgrade", "textual"])
    if r.returncode != 0 or not has_textual(vpy):
        if not quiet:
            sys.stderr.write(_setup_troubleshooting())
        return None
    return vpy


_ONBOARD_INTRO = (
    "\n  Welcome to cc-copilot — a read-only sidecar that watches your coding\n"
    "  agents and recaps what changed while you were away.\n\n"
    "  Pick the model that powers its recaps, chat, and `since` summaries.\n"
    "  (The deterministic core — brief / check / observe — needs no model.)\n")


_ANSI_BY_HEX = {
    "#cb7d5b": "38;2;203;125;91",
    "#347ff2": "38;2;52;127;242",
    "#8b5cf6": "38;2;139;92;246",
}


def _ansi_label(text: str, hex_color: str = "", stream=None) -> str:
    stream = stream or sys.stdout
    if not hex_color or os.environ.get("NO_COLOR"):
        return text
    if not getattr(stream, "isatty", lambda: False)():
        return text
    code = _ANSI_BY_HEX.get(hex_color.lower())
    return f"\033[1;{code}m{text}\033[0m" if code else text


def _run_terminal_onboard(args=None) -> int:
    """Line-based first-run wizard (mirrors the cockpit's WelcomeScreen). Writes
    ~/.cc-copilot.toml and applies the choice to this process."""
    import getpass
    from . import onboard as OB, config as CFG, narrate as N
    detected = OB.detect()
    sys.stderr.write(_ONBOARD_INTRO + "\n")
    for i, d in enumerate(detected, 1):
        mark = "✓" if d.ready else "·"
        label = _ansi_label(f"{d.choice.label:<13}", d.choice.brand_hex, sys.stderr)
        sys.stderr.write(f"   {i}) {label} {mark} {d.status}\n")
    sys.stderr.write("\n")
    # default to the first ready CLI backend (no key needed), else the first row.
    default_idx = next((i for i, d in enumerate(detected, 1)
                        if d.choice.kind == "cli" and d.ready), 1)
    raw = ""
    if sys.stdin.isatty():
        try:
            raw = input(f"  choice [1-{len(detected)}] (default {default_idx}): ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.stderr.write("\n  cancelled — using the default for now.\n")
            return 1
    try:
        idx = int(raw) if raw else default_idx
    except ValueError:
        idx = default_idx
    if not 1 <= idx <= len(detected):
        idx = default_idx
    c = detected[idx - 1].choice

    key_value, model = "", ""
    if c.kind == "api":
        if not os.environ.get(c.key_env) and sys.stdin.isatty():
            try:
                key_value = getpass.getpass(
                    f"  {c.label} API key ({c.key_env}, hidden — paste & Enter): ").strip()
            except (EOFError, KeyboardInterrupt):
                sys.stderr.write("\n  cancelled.\n")
                return 1
        if c.default_model and sys.stdin.isatty():
            from . import models as MODELS
            curated = MODELS.models_for(c.name)
            if curated:
                sys.stderr.write(f"\n  models for {c.label}:\n")
                for j, mi in enumerate(curated, 1):
                    note = f" — {mi.note}" if mi.note else ""
                    sys.stderr.write(f"   {j}) {mi.id}{note}\n")
            try:
                m = input(f"  model [{c.default_model}] (number or any id): ").strip()
            except (EOFError, KeyboardInterrupt):
                m = ""
            if m.isdigit() and curated and 1 <= int(m) <= len(curated):
                model = curated[int(m) - 1].id
            else:
                model = m or c.default_model
        else:
            model = c.default_model

    name = c.name or "skip"
    OB.write_choice(name, model=model, key_value=key_value)
    OB.apply_to_env(name, model=model, key_value=key_value)
    # Propagate into the caller's args so a session built right after (first-run
    # `chat`) uses the choice now — the named API backends don't read
    # CC_COPILOT_MODEL, so writing config/env alone wouldn't take until relaunch.
    if args is not None and c.kind != "skip":
        if hasattr(args, "backend") and getattr(args, "backend") is None:
            args.backend = name
        if hasattr(args, "model") and getattr(args, "model") is None and model:
            args.model = model
    if c.kind == "skip":
        print(f"\n  ✓ saved {CFG.path()} — no model set; the default ({N.backend_name()}) "
              f"applies.\n    Run `cc-copilot init` anytime to choose one.")
    else:
        suffix = f" · model {model}" if model else ""
        print(f"\n  ✓ saved {CFG.path()} (chmod 600) · backend → {c.label}{suffix}")
        if c.kind == "api" and not (key_value or os.environ.get(c.key_env)):
            print(f"    note: no {c.key_env} yet — set it in the config or environment "
                  f"before the model can answer.")
        else:
            print(f"    active backend: {N.backend_name()}")
    print("  launch the cockpit:  cc-copilot cockpit\n")
    return 0


def cmd_init(args) -> int:
    from . import config as CFG
    p = CFG.path()
    if os.path.isfile(p) and not getattr(args, "force", False):
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(f"config already exists: {p}\n"
                  f"  re-run with --force to reconfigure, or edit it directly.")
            return 0
        try:
            ans = input(f"config exists at {p} — reconfigure it? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans not in ("y", "yes"):
            print("left untouched.")
            return 0
    return _run_terminal_onboard(args)


def _maybe_first_run_nudge() -> None:
    """A one-line, non-blocking hint shown on the LLM commands until the user
    runs `init`. Never fires in scripts/hooks (gated on an interactive stdout)."""
    from . import onboard as OB
    if OB.needs_onboarding() and sys.stdout.isatty():
        sys.stderr.write(
            "# cc-copilot: first run — no model configured yet (using the default).\n"
            "#   run `cc-copilot init` to pick Claude / Codex / an API key.\n")


def cmd_setup(args) -> int:
    import subprocess
    vpy = _ensure_tui_runtime()
    if not vpy:
        return 1
    subprocess.run([vpy, "-c",
                    "import textual; print('cockpit ready · textual', textual.__version__)"])
    print("run:  cc-copilot cockpit")
    return 0


def _resolve_or_die(args) -> str:
    cwd = args.cwd or os.getcwd()
    if getattr(args, "here", False):
        p = SRC.current_session_path()
        if p:
            return p
        sys.stderr.write(
            "cc-copilot: --here needs to run inside a live agent session "
            "(no current Claude/Codex session id was found, or its transcript "
            "wasn't found).\n")
        sys.exit(2)
    latest = bool(getattr(args, "latest", False))
    session = None if latest else getattr(args, "session", None)
    path = SRC.resolve(cwd, session, include_current=latest)
    if not path:
        cmd = getattr(args, "cmd", None) or "cockpit"
        sys.stderr.write(f"cc-copilot: no agent session in {cwd!r} "
                         f"(it watches another project's agent, not this dir).\n")
        projs = SRC.projects_with_sessions()
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


def _wait_for_next_session(cwd: str, poll: float = 0.7) -> str:
    """Block until the project's session picture *changes*: a new transcript
    appears, or the newest one grows (``claude --resume`` appends in place).

    ``--next`` exists for `launch`: the cockpit comes up alongside a freshly
    started agent, and "most recent transcript" at that instant is yesterday's
    session — or one the user quit seconds ago. Recency can't tell a dead
    transcript from the coming one, so we snapshot and wait for change. With
    several agents live in one project the first to write wins; `/use` re-pins.
    """
    def snap():
        path = SRC.resolve(cwd, None)
        if not path:
            return None
        try:
            return (path, os.path.getmtime(path))
        except OSError:
            return None  # vanished between resolve and stat; treat as absent

    base = snap()
    told = False
    while True:
        cur = snap()
        if cur is not None and cur != base:
            return cur[0]
        if not told:
            sys.stderr.write(f"cc-copilot: waiting for an agent session in {cwd} "
                             "(Ctrl-C to stop) …\n")
            told = True
        time.sleep(poll)


def cmd_sessions(args) -> int:
    cwd = args.cwd or os.getcwd()
    all_refs = SRC.list_sessions(cwd, include_own=True)
    refs = [r for r in all_refs if r.own] if getattr(args, "helpers", False) \
        else [r for r in all_refs if not r.own]
    hidden = len([r for r in all_refs if r.own]) if not getattr(args, "helpers", False) else 0
    hnote = f"  [{hidden} cc-copilot helper session(s) hidden; --helpers to show]" if hidden else ""
    if not refs:
        print(f"(no work sessions for {cwd}){hnote}\n  dir: {locate.project_dir_for(cwd)}")
        return 1
    print(f"sessions for {cwd}  ({len(refs)}){hnote}:")
    multi_agent = len({r.agent for r in refs}) > 1
    for r in refs:
        title = f"  {r.title}" if r.title else ""
        tag = f"{r.agent:<6} " if multi_agent else ""
        print(f"  {tag}{r.session_id}  {r.hhmm}  {r.size / 1024:7.0f} KB{title}")
    return 0


def cmd_history(args) -> int:
    from . import store as ST
    if not ST.enabled():
        print("resume is disabled (set [history] enabled = true in ~/.cc-copilot.toml, "
              "or unset CC_COPILOT_HISTORY)")
        return 1
    cwd = None if getattr(args, "all", False) else (args.cwd or os.getcwd())
    headers = ST.list_conversations(cwd)
    if not headers:
        scope = "any project" if cwd is None else cwd
        print(f"(no resumable cockpit sessions for {scope})\n  state dir: {ST.state_home()}")
        return 1
    scope = "all projects" if cwd is None else cwd
    print(f"resumable cockpit sessions — {scope}  ({len(headers)}):")
    for h in headers:
        gone = "  (transcript gone)" if not h.transcript_present else ""
        proj = os.path.basename(h.cwd) or "?"
        print(f"  {h.conv_id[:8]}  {locate.ago(h.updated):>5} ago  {h.turns:>3}t  "
              f"{(h.title or '(untitled)')[:40]:<40}  {proj}{gone}")
    return 0


def _load(args):
    path = _resolve_or_die(args)
    tr = SRC.parse(path)
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
        _maybe_first_run_nudge()
        from . import narrate as N
        be = getattr(args, "backend", None)
        if not N.available(be):
            sys.stderr.write(f"# --narrate: backend unavailable ({N.backend_name(be)}); "
                             f"showing brief only — see `cc-copilot backends`\n")
        else:
            sys.stderr.write(f"# narrating via {N.backend_name(be)} …\n")
            try:
                _stream_out(N.narrate_brief_stream(ev.text,
                                                   model=getattr(args, "model", None),
                                                   backend=be),
                            header="\n## 🗣 Narration  _(LLM, grounded in the "
                                   "cited facts above)_\n\n")
            except Exception as e:
                sys.stderr.write(f"# narration failed: {e}\n")
    return 0


def _stream_out(handle, header: str = None) -> None:
    """Print a narrate StreamHandle's chunks to stdout as they arrive.
    ``header`` is printed just before the first chunk — so a stream that dies
    before producing anything doesn't leave an orphaned heading."""
    first = True
    try:
        for chunk in handle:
            if first and header:
                sys.stdout.write(header)
                first = False
            sys.stdout.write(chunk)
            sys.stdout.flush()
        sys.stdout.write("\n")
        sys.stdout.flush()
    except BrokenPipeError:
        # the downstream consumer closed early (`cc-copilot ask … | head`) —
        # normal Unix flow, not an error. Point stdout at /dev/null so the
        # interpreter's exit-time flush doesn't print a second traceback.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except Exception:
            pass


def cmd_ask(args) -> int:
    _maybe_first_run_nudge()
    path, tr, st = _load(args)
    from . import narrate as N
    be = getattr(args, "backend", None)
    if not N.available(be):
        sys.stderr.write(f"cc-copilot: backend unavailable ({N.backend_name(be)}). "
                         f"Run `cc-copilot backends` to see options.\n")
        return 2
    try:
        ctx = EC.build(path, st, getattr(args, "scope", SC.SESSION),
                       sessions=getattr(args, "scope_sessions", ""),
                       question=args.question, history=[], project_context=True)
    except ValueError as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 2
    sys.stderr.write(f"# {N.backend_name(be)} (grounded in expanded evidence context) …\n")
    try:
        _stream_out(N.ask_brief_stream(ctx.text, args.question,
                                       model=getattr(args, "model", None), backend=be))
    except Exception as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 1
    return 0


def cmd_backends(args) -> int:
    from . import backends as BK
    from . import models as MODELS
    from . import narrate as N
    from . import onboard as OB
    active_error = ""
    try:
        active = BK.resolve(getattr(args, "backend", None)).name
    except BK.BackendError as e:
        active = ""
        active_error = str(e)
        sys.stderr.write(f"cc-copilot: {active_error}\n")

    def status_for(be):
        if not be.available():
            return False, f"needs: {be.reason()}"
        if isinstance(be, BK.OpenAICompatBackend) and not be.needs_key:
            ok, why = be.endpoint_health()
            return ok, (why if ok else f"needs: {why}")
        return True, "ready"

    print("LLM backends (default selection marked ▶; the deterministic core needs none):")
    for name, be in sorted(BK.registry().items()):
        ok, status = status_for(be)
        mark = "▶" if name == active else " "
        choice = OB.choice_for_or_none(name)
        label = _ansi_label(f"{name:<11}", choice.brand_hex if choice else "")
        print(f"  {mark} {label} {'✓' if ok else '·'} {status}")
        if getattr(args, "models", False):
            for mi in MODELS.models_for(name):
                note = f" — {mi.note}" if mi.note else ""
                print(f"      · {mi.id}{note}")
    if active_error:
        print(f"\nactive: {active_error}")
    else:
        print(f"\nactive: {N.backend_name(getattr(args, 'backend', None))}")
    print("pick with --backend <name>, env CC_COPILOT_BACKEND, or a custom "
          "CC_COPILOT_LLM_CMD / CC_COPILOT_API_BASE."
          + ("" if getattr(args, "models", False)
             else "  `--models` lists each provider's curated models."))
    return 2 if active_error else 0


def cmd_status(args) -> int:
    from .chat import render_fleet
    cwd = args.cwd or os.getcwd()
    text, n = render_fleet(cwd, limit=args.limit, show_all=getattr(args, "all", False))
    print(text)
    return 0 if n else 1


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


def cmd_observe(args) -> int:
    path, tr, st = _load(args)
    if args.path:
        sys.stderr.write(f"# transcript: {path}\n")
    try:
        print(O.render(path, st, getattr(args, "scope", SC.SESSION),
                       sessions=getattr(args, "scope_sessions", "")))
    except ValueError as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 2
    return 0


def cmd_chat(args) -> int:
    from . import chat as C, onboard as OB
    # First run, plain interactive chat: run the terminal wizard before the REPL
    # (the cockpit gets its own visual WelcomeScreen instead). Skip when a
    # --backend was given explicitly or we're not on a TTY (scripts/hooks).
    if (not getattr(args, "tui", False) and OB.needs_onboarding()
            and getattr(args, "backend", None) is None
            and sys.stdin.isatty() and sys.stdout.isatty()):
        _run_terminal_onboard(args)
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

    if getattr(args, "next", False) and not getattr(args, "session", None):
        try:
            args.session = _wait_for_next_session(args.cwd or os.getcwd())
        except KeyboardInterrupt:
            sys.stderr.write("\n")
            return 130
    path = _resolve_or_die(args)
    try:
        session = C.ChatSession(
            path,
            model=getattr(args, "model", None),
            backend=getattr(args, "backend", None),
            alerts=not getattr(args, "no_alerts", False),
            poll=getattr(args, "poll", 2),
            persist=getattr(args, "persist", None) is not False,  # None/True persist; False off
            scope=getattr(args, "scope", SC.SESSION),
            scope_sessions=getattr(args, "scope_sessions", ""),
        )
    except ValueError as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 2
    if getattr(args, "tui", False):
        from . import tui
        tui.run(session, poll=getattr(args, "poll", 2),
                alerts=not getattr(args, "no_alerts", False))
        return 0
    session.loop()
    return 0


def _cockpit_argv(cwd: str) -> list:
    """How the cockpit pane should invoke us: the installed entry point when
    there is one — but a source checkout must relaunch ITSELF, not whatever
    older version happens to be installed."""
    import shutil
    exe = None if _is_source_checkout() else shutil.which("cc-copilot")
    base = [exe] if exe else [sys.executable, "-m", "cccopilot"]
    return base + ["cockpit", "--next", "--cwd", cwd]


def _cockpit_sh(cwd: str) -> str:
    """The cockpit invocation as a tmux shell-command string.

    Panes inherit the tmux *server's* environment, not ours — so this can't
    rely on PATH (shutil.which gives an absolute path) and a source checkout
    must carry its own PYTHONPATH.
    """
    import shlex
    argv = _cockpit_argv(cwd)
    sh = " ".join(shlex.quote(a) for a in argv)
    if argv[1] == "-m":   # no installed entry point: running from the repo
        # `env`, not bare VAR=… — tmux hands this string to the user's
        # default-shell, and fish/tcsh reject POSIX assignment prefixes.
        sh = f"env PYTHONPATH={shlex.quote(_repo_root())} PYTHONSAFEPATH=1 {sh}"
    return sh


_COCKPIT_WIDTH = "33%"   # agent : cockpit = 2 : 1 — the agent is the main act


def _launch_plan(agent_argv: list, cwd: str, cockpit_sh: str,
                 inside_tmux: bool, session_name: str = "cc-copilot"):
    """The tmux calls for `launch`, as data: (setup argvs, final exec argv).

    Pure so it's testable without tmux. tmux shell-commands are single
    strings run by the user's shell, hence the quoting.
    """
    import shlex
    if inside_tmux:
        # Split the current window; the user's pane (focus stays, -d) becomes
        # the agent via exec, so launch leaves no wrapper process behind. We
        # are a guest in the user's server here — don't touch their options.
        return ([["tmux", "split-window", "-h", "-d", "-l", _COCKPIT_WIDTH,
                  "-c", cwd, cockpit_sh]],
                agent_argv)
    agent_sh = " ".join(shlex.quote(a) for a in agent_argv)
    return ([["tmux", "new-session", "-d", "-s", session_name, "-c", cwd, agent_sh],
             # Our own session: stock tmux ships `mouse off`, where clicking a
             # pane does nothing — a user who doesn't live in tmux literally
             # cannot reach the cockpit pane. Click-to-focus and wheel scroll
             # should just work in a session we created. Bare name, no "=":
             # set-option's target parser rejects the exact-match prefix, and
             # the session we just created guarantees an exact match anyway.
             ["tmux", "set-option", "-t", session_name, "mouse", "on"],
             ["tmux", "split-window", "-h", "-d", "-l", _COCKPIT_WIDTH,
              "-t", session_name, "-c", cwd, cockpit_sh]],
            ["tmux", "attach-session", "-t", session_name])


def _free_tmux_session(taken) -> str:
    """First of cc-copilot, cc-copilot-2, … for which ``taken(name)`` is False."""
    name = "cc-copilot"
    n = 1
    while taken(name):
        n += 1
        name = f"cc-copilot-{n}"
    return name


def cmd_launch(args) -> int:
    import shutil
    import subprocess
    # realpath, not abspath: agents record their *physical* cwd (macOS /tmp is
    # a symlink), and the cockpit must resolve sessions under the same name.
    cwd = os.path.realpath(args.cwd or os.getcwd())
    if not os.path.isdir(cwd):
        sys.stderr.write(f"cc-copilot: no such directory: {cwd}\n")
        return 2
    if cwd.endswith(";"):
        cwd += os.sep   # tmux splits its command line at args ending in ';'

    # Preflight the cockpit pane: without the [tui] extra it would die on
    # arrival, taking this message with it. (A source checkout bootstraps
    # its own .venv in the pane instead.)
    if not _tui_importable() and not _is_source_checkout():
        sys.stderr.write(
            "cc-copilot: the cockpit needs the [tui] extra — reinstall with: "
            "uv tool install \"cc-copilot[tui]\"\n")
        return 3

    agent_argv = list(args.agent_cmd or [])
    if agent_argv and agent_argv[0] == "--":
        agent_argv = agent_argv[1:]
    if not agent_argv:
        default = next((a for a in ("claude", "codex") if shutil.which(a)), None)
        if not default:
            sys.stderr.write("cc-copilot: no agent found on PATH (looked for "
                             "claude, codex). Try: cc-copilot launch -- <agent-cmd>\n")
            return 2
        agent_argv = [default]
    agent_exe = shutil.which(agent_argv[0])
    if not agent_exe:
        sys.stderr.write(f"cc-copilot: agent {agent_argv[0]!r} not found on PATH\n")
        return 2
    # Absolute: outside tmux the *server's* PATH resolves pane commands, and a
    # long-lived server may predate nvm/~/.local/bin entries.
    agent_argv[0] = agent_exe

    if not shutil.which("tmux"):
        sys.stderr.write("cc-copilot: tmux not found — start the agent in another "
                         "terminal yourself; opening the cockpit only.\n")
        argv = _cockpit_argv(cwd)
        argv.remove("--next")   # no agent was launched; don't wait for one
        env = os.environ
        if argv[1] == "-m":     # source checkout: the child needs the repo on path
            env = {**os.environ, "PYTHONPATH": _repo_root(), "PYTHONSAFEPATH": "1"}
        os.execvpe(argv[0], argv, env)

    inside = bool(os.environ.get("TMUX"))
    name = "cc-copilot"
    if not inside:
        def taken(n: str) -> bool:
            # "=" forces exact match; bare -t prefix-matches ("cc-copilot"
            # reads as taken whenever "cc-copilot-2" exists).
            return subprocess.run(["tmux", "has-session", "-t", "=" + n],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0
        name = _free_tmux_session(taken)

    setup, final = _launch_plan(agent_argv, cwd, _cockpit_sh(cwd), inside, name)
    for i, argv in enumerate(setup):
        r = subprocess.run(argv)
        if r.returncode != 0:
            # Clean up the half-built session — but only when a *later* step
            # failed. If new-session itself failed (e.g. a duplicate-name race
            # with a concurrent launch), the session isn't ours to kill.
            if not inside and i > 0:
                subprocess.run(["tmux", "kill-session", "-t", "=" + name],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            sys.stderr.write(f"cc-copilot: {' '.join(argv[:2])} failed "
                             f"(exit {r.returncode}) — the agent may have "
                             "exited immediately; try running it directly\n")
            return 1
    if inside:
        os.chdir(cwd)            # the agent execs in place; honor --cwd
    os.execvp(final[0], final)   # replaces this process: the agent (in-tmux)
    return 0                     # or `tmux attach`; unreachable


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
    notify = getattr(args, "notify", False)
    last_size = -1
    prev = None
    base = os.path.basename(path)[:8]
    mode = " · desktop alerts on" if notify else ""
    sys.stderr.write(f"# watching {path} (every {interval}s{mode}, Ctrl-C to stop)\n")
    try:
        while True:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            if size != last_size:
                last_size = size
                try:
                    tr = SRC.parse(path)
                    st = S.build(tr)
                    if notify and prev is not None:
                        from .notify import alert_for_diff, desktop_notify
                        msg = alert_for_diff(S.diff(prev, st))
                        if msg:
                            desktop_notify(f"cc-copilot · {base}", msg)
                    prev = st
                    os.system("clear" if os.name != "nt" else "cls")
                    print(B.render(st))
                    print(f"\n_(watching{mode} · {time.strftime('%H:%M:%S')} · {tr.raw_lines} events)_")
                except OSError:
                    # transcript deleted/rotated mid-watch: force a re-parse on the
                    # next poll instead of leaving last_size committed to a size we
                    # never actually parsed (which would skip it until a later write).
                    last_size = -1
                    sys.stderr.write(f"# transcript unavailable ({time.strftime('%H:%M:%S')}) — waiting…\n")
            time.sleep(interval)
    except KeyboardInterrupt:
        sys.stderr.write("\n# stopped\n")
    return 0


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def cmd_since(args) -> int:
    from . import lastlook as LL, since as SI, narrate as N
    if not getattr(args, "raw", False):
        _maybe_first_run_nudge()
    path = _resolve_or_die(args)
    if getattr(args, "path", False):
        sys.stderr.write(f"# transcript: {path}\n")
    tr = SRC.parse(path)
    st = S.build(tr)
    key = LL.key_for(getattr(st.tr, "session_id", "") or "", path)
    cur_line = tr.records[-1].line if tr.records else 0
    cur_ts = tr.records[-1].raw_ts if tr.records else ""
    when = (getattr(args, "when", None) or "last-look").strip().lower()

    if when in ("last-look", "lastlook", "last"):
        if not LL.enabled():
            print("last-look tracking is off (persistence disabled via "
                  "CC_COPILOT_HISTORY=0 or [history] enabled=false).\n"
                  "Use a time window instead, e.g. `cc-copilot since 30m`.")
            return 0
        mark = LL.get(key)
        if mark is None:
            LL.mark(key, cur_line, cur_ts, _now_iso())
            print(f"No last-look mark for `{key[:8]}` yet — recorded your current "
                  f"position (L{cur_line}).\nRe-run `cc-copilot since` after the agent "
                  f"works to see what changed. (Or `since 30m` for a time window.)")
            return 0
        view = SI.build(tr, st, since_line=int(mark.get("line", 0) or 0), label="last look",
                        looked_at=mark.get("looked_at", ""))
    else:
        secs = SI.parse_duration(when)
        if secs is None:
            sys.stderr.write(f"cc-copilot: unknown time {when!r}; use 'last-look' or a "
                             f"duration like 30m / 2h / 1d\n")
            return 2
        view = SI.build(tr, st, seconds=secs, label=when)

    # Recap by default (LLM narration grounded in the cited delta) with the
    # evidence kept beneath it; `--raw`, no backend, or an empty delta print the
    # deterministic view alone.
    be = getattr(args, "backend", None)
    if getattr(args, "raw", False) or view.nothing_new or not N.available(be):
        print(view.text)
    else:
        sys.stderr.write(f"# recapping via {N.backend_name(be)} …\n")
        try:
            recap = N.recap_since(view.text, model=getattr(args, "model", None), backend=be)
            body = view.text.split("\n", 1)[1] if view.text.startswith("#") else view.text
            lead = f"{view.pending_ask}\n\n" if view.pending_ask else ""
            print(f"# 🛰  recap — since {view.label}\n\n{lead}{recap.strip()}\n\n"
                  f"---\n_evidence — every [L…] is a transcript line:_\n{body}")
        except Exception as e:
            sys.stderr.write(f"# recap failed ({e}); showing evidence\n")
            print(view.text)
    # advance the marker so the next `since` is incremental (unless --peek);
    # forward-only so a slow recap here can't rewind a concurrent cockpit's mark
    if when in ("last-look", "lastlook", "last") and not getattr(args, "peek", False):
        LL.advance(key, cur_line, cur_ts, _now_iso())
    return 0


def cmd_now(args) -> int:
    """Recommend the next step from the completed work: an LLM recommendation
    grounded in the cited evidence, with a deterministic next-step (the
    observer's ranked decision) for `--raw` or when no backend is available."""
    from . import narrate as N
    if not getattr(args, "raw", False):
        _maybe_first_run_nudge()
    path, tr, st = _load(args)
    if getattr(args, "path", False):
        sys.stderr.write(f"# transcript: {path}\n")
    scope = getattr(args, "scope", SC.SESSION)
    sessions = getattr(args, "scope_sessions", "")
    try:
        det = O.next_step(path, st, scope, sessions=sessions)
    except ValueError as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 2
    be = getattr(args, "backend", None)
    if getattr(args, "raw", False) or not N.available(be):
        print(det)
        return 0
    try:
        ev = SC.render_evidence(path, st, scope, sessions=sessions)
    except ValueError as e:
        sys.stderr.write(f"cc-copilot: {e}\n")
        return 2
    sys.stderr.write(f"# next step via {N.backend_name(be)} …\n")
    try:
        _stream_out(N.next_step_brief_stream(ev.text, model=getattr(args, "model", None),
                                             backend=be),
                    header="# 🧭 next step  _(LLM, grounded in the cited evidence)_\n\n")
    except Exception as e:
        sys.stderr.write(f"# next-step failed ({e}); showing deterministic suggestion\n")
        print(det)
    return 0


def cmd_handoff(args) -> int:
    from . import handoff as HO, lastlook as LL, since as SI
    path = _resolve_or_die(args)
    if getattr(args, "path", False):
        sys.stderr.write(f"# transcript: {path}\n")
    tr = SRC.parse(path)
    st = S.build(tr)
    agent = SRC.source_for_path(path).name
    sv = None
    mark = LL.get(LL.key_for(getattr(st.tr, "session_id", "") or "", path))
    if mark is not None:
        sv = SI.build(tr, st, since_line=int(mark.get("line", 0) or 0), label="last look",
                      looked_at=mark.get("looked_at", ""))
    md = HO.render(st, agent=agent, generated_at=time.strftime("%Y-%m-%d %H:%M"),
                   since_view=sv)
    out = getattr(args, "out", None)
    if out:
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(md + "\n")
        except OSError as e:
            sys.stderr.write(f"cc-copilot: could not write {out}: {e}\n")
            return 1
        print(f"wrote handoff → {out}  ({len(md.splitlines())} lines)")
    else:
        print(md)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cc-copilot", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"cc-copilot {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--cwd", help="project dir to resolve sessions for (default: $PWD)")
        sp.add_argument("--agent", action="append", metavar="NAME",
                        help="restrict to an agent's sessions (claude/codex; repeatable). "
                             "Default: every agent with sessions on this machine.")
        sp.add_argument("--here", action="store_true",
                        help="observe the session you're running inside of "
                             "(your current Claude Code session), not another one")

    sp = sub.add_parser("sessions", help="list sessions for this project")
    common(sp)
    sp.add_argument("--helpers", action="store_true",
                    help="show cc-copilot's own narration sessions instead of hiding them")
    sp.set_defaults(func=cmd_sessions)

    sp = sub.add_parser("resume", aliases=["history"],
                        help="list resumable cockpit sessions (newest first)")
    common(sp)
    sp.add_argument("--all", action="store_true",
                    help="every project's cockpit sessions, not just this cwd's")
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
        sp.add_argument("--scope", type=SC.normalize, default=None,
                        help="evidence range: session, multi-session, or project "
                             "(aliases: multi, repo; Q&A always includes project context)")
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
    sp.add_argument("--models", action="store_true",
                    help="also list each provider's curated models")
    sp.set_defaults(func=cmd_backends)

    sp = sub.add_parser("init",
                        help="first-run setup: pick the model (Claude/Codex/API) & save the config")
    sp.add_argument("--force", action="store_true",
                    help="reconfigure even if a config already exists (preserves other keys)")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("config",
                        help="show or scaffold ~/.cc-copilot.toml (default backend/model/keys)")
    sp.add_argument("--init", action="store_true",
                    help="write a starter config file if none exists (see `init` for the wizard)")
    sp.set_defaults(func=cmd_config)

    sp = sub.add_parser("setup",
                        help="install the cockpit TUI extra (creates .venv + textual)")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("check", help="is it safe to continue? (off-track/friction signals)")
    session_args(sp)
    scope_arg(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("observe",
                        help="attention queue + next human decision for the selected scope")
    session_args(sp)
    scope_arg(sp)
    sp.set_defaults(func=cmd_observe)

    sp = sub.add_parser("now",
                        help="recommend the next step from the completed work "
                             "(LLM; --raw = deterministic next-step, no model call)")
    session_args(sp)
    scope_arg(sp)
    sp.add_argument("--raw", action="store_true",
                    help="deterministic next-step only — no LLM recommendation")
    sp.add_argument("--model", help="model for the recommendation (passed to the backend)")
    sp.add_argument("--backend",
                    help="LLM backend (claude/codex/deepseek/ollama/…; see `backends`)")
    sp.set_defaults(func=cmd_now)

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
                        help="don't save/restore this cockpit session (in-memory only)")
        sp.add_argument("--poll", type=int, default=2,
                        help="alert/header poll interval in seconds (default 2)")
        sp.add_argument("--next", action="store_true",
                        help="wait for the next session to start (or resume) in this "
                             "project and attach to it — what `launch` passes, since "
                             "the agent's session may not exist yet")
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

    sp = sub.add_parser("launch", aliases=["up"],
                        help="start an agent and the cockpit side by side (tmux split)",
                        epilog="agent flags go after `--`: "
                               "cc-copilot launch -- claude --resume")
    sp.add_argument("--cwd", help="project dir to launch in (default: $PWD)")
    sp.add_argument("agent_cmd", nargs=argparse.REMAINDER, metavar="[--] AGENT [ARG …]",
                    help="agent command (default: claude, else codex). "
                         "Examples: `launch codex`, `launch -- claude --resume`")
    sp.set_defaults(func=cmd_launch)

    sp = sub.add_parser("state", help="dump the raw state model as JSON")
    session_args(sp)
    sp.add_argument("--json", action="store_true", help="(default output is JSON)")
    sp.set_defaults(func=cmd_state)

    sp = sub.add_parser("watch", help="re-render the brief as the transcript grows")
    session_args(sp)
    sp.add_argument("--interval", type=int, default=5, help="poll seconds (default 5)")
    sp.add_argument("--notify", action="store_true",
                    help="desktop/terminal alert when the agent needs you "
                         "(transition into intervene / stalled / new failure)")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("since",
                        help="what changed since you last looked (or in the last 30m/2h/…)")
    common(sp)
    # `when` is the positional so `cc-copilot since 30m` works; pick the session
    # with --session/--latest (a second optional positional would be ambiguous).
    sp.add_argument("when", nargs="?", default="last-look",
                    help="'last-look' (default) or a duration like 30m / 2h / 1d")
    sp.add_argument("--session", help="session id, id-prefix, or transcript path "
                                      "(default: most recent, excluding the current)")
    sp.add_argument("--latest", action="store_true",
                    help="explicitly target the most recent session")
    sp.add_argument("--path", action="store_true",
                    help="also print the resolved transcript path to stderr")
    sp.add_argument("--peek", action="store_true",
                    help="don't advance the last-look marker after showing")
    sp.add_argument("--raw", action="store_true",
                    help="deterministic cited delta only — no LLM recap")
    sp.add_argument("--model", help="model for the recap (passed to the backend)")
    sp.add_argument("--backend",
                    help="LLM backend for the recap (claude/codex/deepseek/ollama/…; see `backends`)")
    sp.set_defaults(func=cmd_since)

    sp = sub.add_parser("handoff",
                        help="write a shareable Markdown handoff (brief + what changed)")
    session_args(sp)
    sp.add_argument("--out", metavar="FILE", help="write to this file (default: stdout)")
    sp.set_defaults(func=cmd_handoff)

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


def _effective_argv(argv):
    """Bare `cc-copilot` on a terminal means the cockpit; everywhere else
    (scripts, hooks, pipes) keep argparse's usage error."""
    argv = sys.argv[1:] if argv is None else list(argv)
    if (not argv and sys.stdin and sys.stdin.isatty()      # stdin/stdout are None
            and sys.stdout and sys.stdout.isatty()):       # when a daemon closed them
        return ["cockpit"]
    return argv


def main(argv=None) -> int:
    from . import config as CFG
    args = build_parser().parse_args(_effective_argv(argv))
    CFG.apply_defaults(args)   # config file fills gaps the flags/env left
    return args.func(args)
