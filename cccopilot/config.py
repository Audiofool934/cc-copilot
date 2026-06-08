"""Optional config file so you set a default backend / model / keys once.

Looked up at ``$CC_COPILOT_CONFIG`` or ``~/.cc-copilot.toml``. Holds:

    backend = "codex"            # default LLM backend
    model   = "deepseek-reasoner"# default model for it
    [env]                        # exported as env vars (real env wins)
    DEEPSEEK_API_KEY = "sk-…"
    CC_COPILOT_API_BASE = "http://localhost:11434"

Precedence everywhere: explicit CLI flag > real environment variable > this
file > built-in default. The deterministic core (`brief`/`check`) ignores all
of it — only the language features read a backend.
"""

from __future__ import annotations

import os
import sys


TEMPLATE = '''\
# cc-copilot config  (~/.cc-copilot.toml)
# Defaults for the LLM-backed commands (ask / chat / brief --narrate).
# The deterministic core (brief / check / alerts) needs none of this.

# Default backend: codex | claude | deepseek | openai | openrouter | ollama | gemini | llm
backend = "codex"

# Default model for that backend (optional — omit to use the backend's own default)
# model = "deepseek-reasoner"

# Secrets + provider settings, exported as environment variables.
# Real env vars always win over these. Keep this file private (chmod 600).
[env]
# DEEPSEEK_API_KEY = "sk-..."
# OPENAI_API_KEY = "sk-..."
# OPENROUTER_API_KEY = "sk-..."
# Any OpenAI-compatible endpoint (vLLM, LM Studio, Groq, a proxy, …):
# CC_COPILOT_API_BASE = "http://localhost:11434"
# CC_COPILOT_API_KEY = "..."
# CC_COPILOT_MODEL = "qwen2.5"

# Persist your copilot Q&A so switching sessions / relaunching restores prior
# chats. Stored locally under $CC_COPILOT_STATE_DIR (default ~/.local/state/
# cc-copilot), dir 0700 / files 0600. These files hold your questions and the
# copilot's answers in plaintext — set enabled = false to keep everything in
# memory only. Never written under ~/.claude.
[history]
enabled = true
# dir = "~/.local/state/cc-copilot"   # or set $CC_COPILOT_STATE_DIR

# Which coding agents to observe. Default: every agent whose storage exists on
# this machine (Claude Code under ~/.claude, Codex under ~/.codex). One cockpit
# can watch sessions from several agents at once, grouped by project. Restrict
# the set here, or per-invocation with --agent / $CC_COPILOT_AGENTS.
# [agents]
# enabled = ["claude", "codex"]
'''


def path() -> str:
    return os.environ.get("CC_COPILOT_CONFIG") or os.path.expanduser("~/.cc-copilot.toml")


def _load_simple(p: str) -> dict:
    """Minimal fallback parser (key = "value", [tables], # comments) for Pythons
    without ``tomllib``. Every ``[section]`` nests into its own dict, so e.g.
    ``[history] enabled = false`` reads back as ``data["history"]["enabled"]``."""
    data, section = {}, None
    try:
        with open(p, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return {}
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip()
            data.setdefault(section, {})
            continue
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v.lower() in ("true", "false"):
            v = v.lower() == "true"
        target = data[section] if section is not None else data
        target[k] = v
    return data


def load() -> dict:
    """Parse the config and export its ``[env]`` table (without clobbering real
    env vars). Returns the top-level settings dict."""
    p = path()
    if not os.path.isfile(p):
        return {}
    try:
        import tomllib
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except ImportError:
        data = _load_simple(p)
    except Exception as e:  # malformed TOML — warn, don't crash
        sys.stderr.write(f"# cc-copilot: ignoring malformed config {p}: {e}\n")
        return {}
    env = data.get("env")
    if isinstance(env, dict):
        for k, v in env.items():
            os.environ.setdefault(str(k), str(v))  # real env wins
    return data


def apply_defaults(args) -> None:
    """Fill argparse defaults from the config, only where the user didn't say."""
    data = load()
    # backend: surface as CC_COPILOT_BACKEND so resolve() picks it up when no
    # --backend flag and no real env var were given.
    if getattr(args, "backend", None) is None and not os.environ.get("CC_COPILOT_BACKEND"):
        b = data.get("backend")
        if b:
            os.environ["CC_COPILOT_BACKEND"] = str(b)
    # model: a plain default when --model wasn't passed.
    if hasattr(args, "model") and getattr(args, "model", None) is None and data.get("model"):
        args.model = str(data["model"])
    # state dir: surface [history].dir as CC_COPILOT_STATE_DIR (mirrors backend).
    if not os.environ.get("CC_COPILOT_STATE_DIR"):
        h = data.get("history")
        d = h.get("dir") if isinstance(h, dict) else None
        if d:
            os.environ["CC_COPILOT_STATE_DIR"] = os.path.expanduser(str(d))
    # persist: fill the chat/cockpit toggle from [history].enabled when unset.
    if hasattr(args, "persist") and getattr(args, "persist", None) is None:
        args.persist = history_enabled()
    # agent filter: surface --agent as CC_COPILOT_AGENTS so the sources
    # dispatcher scopes discovery (mirrors how --backend is surfaced).
    agent = getattr(args, "agent", None)
    if agent:
        os.environ["CC_COPILOT_AGENTS"] = ",".join(agent)


def history_enabled() -> bool:
    """Whether to persist copilot conversations. Env wins, then the file, then on.

    ``CC_COPILOT_HISTORY=0|false|no|off`` (or empty) forces it off for a single
    invocation; otherwise ``[history].enabled`` from the config decides; default on.
    """
    env = os.environ.get("CC_COPILOT_HISTORY")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off", "")
    h = load().get("history")
    if isinstance(h, dict) and "enabled" in h:
        return bool(h["enabled"])
    return True


def agents_enabled():
    """Agent sources to observe, or ``None`` for 'all available'.

    Reads ``[agents] enabled`` from the config. Accepts a TOML array
    (``["claude", "codex"]``) or, under the no-tomllib fallback parser, a
    comma/space string. ``$CC_COPILOT_AGENTS`` is handled in the sources
    dispatcher and takes precedence over this.
    """
    a = load().get("agents")
    if isinstance(a, dict) and a.get("enabled") is not None:
        v = a["enabled"]
        if isinstance(v, (list, tuple)):
            names = [str(x).strip().lower() for x in v if str(x).strip()]
        else:
            names = [p.strip().lower() for p in str(v).replace(",", " ").split() if p.strip()]
        return names or None
    return None


def init_file() -> str:
    """Write the template to the config path if absent. Returns a status line."""
    p = path()
    if os.path.exists(p):
        return f"config already exists: {p} (left untouched)"
    with open(p, "w", encoding="utf-8") as f:
        f.write(TEMPLATE)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return f"wrote starter config: {p} (chmod 600) — edit it to set backend/model/keys"
