"""First-run onboarding: pick the LLM backend (and capture an API key) once,
then write ``~/.cc-copilot.toml`` so it never asks again.

The config file's *existence* is the "already onboarded" sentinel — the moment a
file is written (even a "Skip" that picks nothing), :func:`needs_onboarding`
returns False and nothing prompts again. The logic here is UI-agnostic: the
terminal wizard (``cc-copilot init``) and the cockpit's ``WelcomeScreen`` both
call :func:`detect` / :func:`write_choice` / :func:`apply_to_env`, so the two
surfaces can never drift.

What "model" means here maps straight onto :mod:`cccopilot.backends`:
  - **Claude** / **Codex** are *CLI* backends — auth is the CLI's own (your
    Claude subscription, your ChatGPT login); **no API key** is stored.
  - **OpenAI / DeepSeek / OpenRouter** are OpenAI-compatible *API* backends —
    they need a key, which we write into the ``[env]`` table (chmod 600).
  - **Skip** writes a config with no backend chosen; the default (``codex``)
    applies and the wizard stops nagging.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import config as CFG, backends as BK


@dataclass
class Choice:
    name: str                 # backend name in backends.registry() ("" for skip)
    label: str                # friendly display name ("Claude", "OpenAI", …)
    kind: str                 # "cli" | "api" | "skip"
    blurb: str                # one-line description of the auth model
    key_env: str = ""         # env var the API key lives under (api only)
    default_model: str = ""   # prefilled, editable; "" → backend's own default
    brand_hex: str = ""       # identity hue for the TUI row


# Curated, friendly subset of backends.registry(), in presentation order. We
# don't surface every backend (ollama/llm/custom stay power-user, file-only) —
# onboarding is the common path, not the exhaustive one.
CHOICES = [
    Choice("claude", "Claude", "cli",
           "uses your Claude Code subscription — no API key", brand_hex="#cb7d5b"),
    Choice("codex", "Codex", "cli",
           "uses your ChatGPT (Codex) login — no API key", brand_hex="#347ff2"),
    Choice("openai", "OpenAI", "api",
           "OpenAI API — needs a key", key_env="OPENAI_API_KEY",
           default_model="gpt-4o"),
    Choice("deepseek", "DeepSeek", "api",
           "DeepSeek API — needs a key", key_env="DEEPSEEK_API_KEY",
           default_model="deepseek-chat"),
    Choice("openrouter", "OpenRouter", "api",
           "OpenRouter API — needs a key", key_env="OPENROUTER_API_KEY",
           default_model="openai/gpt-4o"),
    Choice("", "Skip for now", "skip",
           "deterministic recaps only — set a model later with `cc-copilot init`"),
]


@dataclass
class Detected:
    choice: Choice
    ready: bool               # usable right now (CLI on PATH, or key already set)
    status: str               # short human status line


def choice_for(name: str) -> Choice:
    key = (name or "").strip().lower()
    for c in CHOICES:
        if c.name == key:
            return c
    if key in ("skip", ""):
        return CHOICES[-1]
    raise ValueError(f"unknown onboarding choice {name!r}")


def choice_for_or_none(name: str):
    """Like :func:`choice_for` but returns None for backends outside the curated
    set (ollama / custom / gemini / llm) instead of raising — callers that switch
    to *any* backend use this to ask "is this a key-needing API provider?"."""
    try:
        c = choice_for(name)
    except ValueError:
        return None
    return c if c.kind != "skip" else None


def detect() -> list:
    """Each curated choice annotated with whether it's usable on this machine."""
    reg = BK.registry()
    out = []
    for c in CHOICES:
        if c.kind == "skip":
            out.append(Detected(c, True, "no model — recaps show the cited evidence only"))
            continue
        be = reg.get(c.name)
        ready = bool(be) and be.available()
        if c.kind == "cli":
            status = ("ready · " + c.blurb.split("—")[0].strip()) if ready \
                else f"`{c.name}` CLI not found on PATH — install it to use this"
        else:  # api
            status = f"key set · {c.key_env}" if ready \
                else f"needs key · {c.key_env}"
        out.append(Detected(c, ready, status))
    return out


def _disabled() -> bool:
    v = os.environ.get("CC_COPILOT_NO_ONBOARD", "")
    return v.strip().lower() not in ("", "0", "false", "no", "off")


def needs_onboarding() -> bool:
    """True on the first run only: no config file yet, and not opted out via
    ``CC_COPILOT_NO_ONBOARD``. Writing any config (including a Skip) flips this
    to False permanently."""
    if _disabled():
        return False
    return not os.path.isfile(CFG.path())


# ── config rendering ─────────────────────────────────────────────────────

def _read_existing(path: str) -> dict:
    """Parse an existing config WITHOUT the env-export side effect of
    ``config.load`` — we only want to preserve the user's other settings when a
    re-run rewrites the file."""
    if not os.path.isfile(path):
        return {}
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        return CFG._load_simple(path)
    except Exception:
        return {}


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def _existing_agents(existing: dict):
    """The user's ``[agents] enabled`` list from a parsed config, or None.
    Accepts a TOML array (tomllib) or a comma/space string (fallback parser)."""
    a = existing.get("agents")
    if not isinstance(a, dict) or a.get("enabled") is None:
        return None
    v = a["enabled"]
    if isinstance(v, (list, tuple)):
        names = [str(x).strip() for x in v if str(x).strip()]
    else:
        # the no-tomllib fallback parser hands back the raw array text
        # (e.g. '["claude", "codex"]') as a string — unwrap it ourselves.
        s = str(v).strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        names = [t.strip().strip('"').strip("'")
                 for t in s.replace(",", " ").split()]
        names = [n for n in names if n]
    return names or None


def render_config(backend, model="", env=None, history_enabled=True,
                  history_dir="", agents_enabled=None) -> str:
    """Produce a clean, commented ``~/.cc-copilot.toml`` reflecting a choice.

    ``backend`` is a backend name, or None for "skip" (the default applies and a
    note explains how to set one). ``env`` is a dict of secrets to write active
    in ``[env]`` (real env still wins at load time)."""
    env = dict(env or {})
    lines = [
        "# cc-copilot config  (~/.cc-copilot.toml)",
        "# Written by `cc-copilot init` — edit freely; re-run it to change.",
        "# Defaults for the LLM-backed commands (ask / chat / cockpit / since",
        "# recap / brief --narrate). The deterministic core needs none of this.",
        "",
        "# backend: claude | codex | openai | deepseek | openrouter | ollama | llm | gemini",
    ]
    if backend:
        lines.append(f'backend = "{_esc(backend)}"')
    else:
        lines.append('# backend = "codex"   # not chosen yet — the default (codex) '
                     'applies; run `cc-copilot init` to pick one')
    lines.append("")
    if model:
        # NB: keep value lines free of trailing inline comments — the no-tomllib
        # fallback parser (config._load_simple, used on Python < 3.11) doesn't
        # strip them, so an inline comment would leak into the parsed value.
        lines.append("# the model for that backend:")
        lines.append(f'model = "{_esc(model)}"')
    else:
        lines.append("# model = \"...\"   # optional — omit to use the backend's own default")
    lines += [
        "",
        "# Secrets + provider settings, exported as environment variables.",
        "# Real env vars always win over these. Keep this file private (chmod 600).",
        "[env]",
    ]
    for k in sorted(env):
        lines.append(f'{k} = "{_esc(env[k])}"')
    # discoverable examples for the providers not already set
    for ex in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
        if ex not in env:
            lines.append(f'# {ex} = "sk-..."')
    lines += [
        "# Any OpenAI-compatible endpoint (vLLM, LM Studio, Ollama, a proxy, …):",
        "# CC_COPILOT_API_BASE = \"http://localhost:11434\"",
        "# CC_COPILOT_API_KEY = \"...\"",
        "# CC_COPILOT_MODEL = \"qwen2.5\"",
        "",
        "# Persist your copilot Q&A so relaunching restores prior chats. Stored",
        "# locally under $CC_COPILOT_STATE_DIR (0700/0600), never under ~/.claude.",
        "[history]",
        f"enabled = {'true' if history_enabled else 'false'}",
    ]
    if history_dir:
        lines.append(f'dir = "{_esc(history_dir)}"')
    lines += [
        "",
        "# Which coding agents to observe (default: every one found on this machine).",
    ]
    if agents_enabled:
        arr = ", ".join(f'"{_esc(a)}"' for a in agents_enabled)
        lines += ["[agents]", f"enabled = [{arr}]"]
    else:
        lines += ["# [agents]", '# enabled = ["claude", "codex"]']
    lines.append("")
    return "\n".join(lines)


def write_choice(name, model="", key_value="", path=None) -> str:
    """Write the config for a chosen backend, preserving any existing ``[env]``
    secrets and the history toggle. Returns the path written (chmod 600)."""
    c = choice_for(name)
    p = path or CFG.path()
    existing = _read_existing(p)
    env = dict(existing.get("env") or {})
    if c.kind == "api" and key_value:
        env[c.key_env] = key_value
    # Preserve every non-model setting the user may have curated, so changing
    # provider via `init --force` never silently moves their saved history or
    # re-enables agents they had turned off.
    hist = existing.get("history") if isinstance(existing.get("history"), dict) else {}
    hist_enabled = bool(hist.get("enabled", True))
    hist_dir = str(hist.get("dir") or "")
    agents_enabled = _existing_agents(existing)
    text = render_config(backend=(c.name or None), model=model, env=env,
                         history_enabled=hist_enabled, history_dir=hist_dir,
                         agents_enabled=agents_enabled)
    # Create the temp file 0600 from the start (mkstemp honors that, ignoring
    # umask) so an API key never lands in a world-readable file — not even in the
    # window before chmod, and not if we crash mid-write. Same dir as the target
    # so os.replace stays atomic (same filesystem).
    import tempfile
    d = os.path.dirname(p) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".cc-copilot-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(p, 0o600)        # also tighten if an older file was looser
    except OSError:
        pass
    return p


def apply_to_env(name, model="", key_value="") -> None:
    """Make a freshly-written choice take effect in THIS process, so a running
    cockpit / chat uses it immediately without a relaunch. Mirrors the file:
    surface the backend as ``CC_COPILOT_BACKEND`` and any key as its env var."""
    c = choice_for(name)
    if c.kind == "skip":
        return
    os.environ["CC_COPILOT_BACKEND"] = c.name
    if c.kind == "api" and key_value:
        os.environ[c.key_env] = key_value
    if model:
        os.environ["CC_COPILOT_MODEL"] = model
