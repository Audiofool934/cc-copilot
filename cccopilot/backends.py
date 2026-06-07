"""Pluggable LLM backends for the narration / chat layer.

cc-copilot's deterministic core uses no model; only `ask`/`chat`/`--narrate`
call an LLM, and they don't care which one. Two backend shapes, both zero-dep:

- **CliBackend** — shells out to a local agent CLI that takes the prompt as its
  last argument and prints the answer to stdout (`claude -p`, `codex exec`,
  `gemini -p`, `llm`). Auth is the CLI's own (e.g. `codex login` = ChatGPT OAuth,
  `claude` = your Claude subscription) — cc-copilot never touches credentials.
- **OpenAICompatBackend** — a stdlib-only POST to any OpenAI-compatible
  `/chat/completions` endpoint (DeepSeek, OpenAI, OpenRouter, Ollama, vLLM, …),
  auth via an API-key env var.

Selection precedence (see :func:`resolve`):
  explicit name  >  CC_COPILOT_BACKEND  >  CC_COPILOT_LLM_CMD (custom CLI)
  >  CC_COPILOT_API_BASE (custom OpenAI-compatible)  >  default `codex`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request


class BackendError(RuntimeError):
    pass


class Backend:
    name = "?"

    def available(self) -> bool:
        return False

    def reason(self) -> str:
        """Why it's unavailable (for a helpful message)."""
        return ""

    def complete(self, prompt: str, model: str = None, timeout: int = 180) -> str:
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


# ── CLI backends ─────────────────────────────────────────────────────────

class CliBackend(Backend):
    def __init__(self, name, argv, model_args=None, cwd=None):
        self.name = name
        self.argv = [a for a in argv if a]
        # model_args: callable(model)->list[str], inserted before the prompt
        self.model_args = model_args
        # cwd: run the CLI here. For agent CLIs (claude/codex) that log a
        # session transcript per call, a neutral dir keeps those out of the
        # user's project session list — and narration wants no repo context.
        self.cwd = cwd

    def _bin(self):
        return self.argv[0] if self.argv else ""

    def available(self) -> bool:
        b = self._bin()
        return bool(b) and (os.path.isfile(b) or shutil.which(b) is not None)

    def reason(self) -> str:
        return f"`{self._bin()}` not found on PATH"

    def complete(self, prompt, model=None, timeout=180) -> str:
        argv = list(self.argv)
        if model and self.model_args:
            argv += list(self.model_args(model))
        argv += [prompt]
        try:
            # pin UTF-8 (don't trust the locale): the prompt and the model's
            # reply routinely carry CJK / emoji / accented text, and a C/POSIX
            # locale would otherwise decode stdout as ASCII and mangle or crash.
            p = subprocess.run(argv, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=self.cwd)
        except FileNotFoundError:
            raise BackendError(self.reason())
        except subprocess.TimeoutExpired:
            raise BackendError(f"{self.name} timed out after {timeout}s")
        if p.returncode != 0:
            raise BackendError(p.stderr.strip() or f"{self.name} exited {p.returncode}")
        out = p.stdout.strip()
        if not out:
            raise BackendError(f"{self.name} returned no output")
        return out

    def describe(self) -> str:
        # show the command, not the full resolved path (fnm/venv paths are noisy)
        shown = " ".join([os.path.basename(self.argv[0])] + self.argv[1:]) if self.argv else self.name
        return f"{self.name} (cli: {shown})"


# ── OpenAI-compatible HTTP backend ───────────────────────────────────────

class OpenAICompatBackend(Backend):
    def __init__(self, name, endpoint, key_env, default_model, needs_key=True):
        self.name = name
        self.endpoint = endpoint
        self.key_env = key_env
        self.default_model = default_model
        self.needs_key = needs_key

    def _key(self) -> str:
        return os.environ.get(self.key_env, "") if self.key_env else ""

    def available(self) -> bool:
        return (not self.needs_key) or bool(self._key())

    def reason(self) -> str:
        return f"set {self.key_env}" if self.needs_key else "endpoint unreachable"

    def complete(self, prompt, model=None, timeout=180) -> str:
        headers = {"Content-Type": "application/json"}
        key = self._key()
        if self.needs_key and not key:
            raise BackendError(f"set {self.key_env} to use the {self.name} backend")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = json.dumps({
            "model": model or self.default_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")[:300]
            except Exception:
                pass
            raise BackendError(f"{self.name} HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise BackendError(f"{self.name} connection error: {getattr(e, 'reason', e)}")
        except (TimeoutError, OSError) as e:
            raise BackendError(f"{self.name} request failed: {e}")
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            raise BackendError(f"{self.name} unexpected response: "
                               f"{json.dumps(data, ensure_ascii=False)[:200]}")

    def describe(self) -> str:
        k = f", key ${self.key_env}" if self.needs_key else ", no key"
        return f"{self.name} (api: {self.endpoint}, model {self.default_model}{k})"


# ── registry ─────────────────────────────────────────────────────────────

def _claude_bin() -> str:
    cand = os.path.expanduser("~/.local/bin/claude")
    if os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    return shutil.which("claude") or "claude"


def registry() -> dict:
    """Built fresh each call so env / PATH changes are picked up."""
    reg = {
        # agent CLIs — auth is the CLI's own (claude subscription, codex OAuth …).
        # cwd=tmp so their per-call session logs don't pollute your project list.
        "claude": CliBackend("claude", [_claude_bin(), "-p"],
                             model_args=lambda m: ["--model", m],
                             cwd=tempfile.gettempdir()),
        # --skip-git-repo-check: we run codex in a neutral temp dir (not the
        # watched repo), so it must not insist on being inside a git project.
        "codex":  CliBackend("codex", [shutil.which("codex") or "codex", "exec",
                                       "--skip-git-repo-check"],
                             model_args=lambda m: ["-c", f"model={m}"],
                             cwd=tempfile.gettempdir()),
        "gemini": CliBackend("gemini", [shutil.which("gemini") or "gemini", "-p"],
                             model_args=lambda m: ["-m", m]),
        "llm":    CliBackend("llm", [shutil.which("llm") or "llm"],
                             model_args=lambda m: ["-m", m]),
        # OpenAI-compatible HTTP APIs
        "deepseek":   OpenAICompatBackend("deepseek", "https://api.deepseek.com/chat/completions",
                                          "DEEPSEEK_API_KEY", "deepseek-chat"),
        "openai":     OpenAICompatBackend("openai", "https://api.openai.com/v1/chat/completions",
                                          "OPENAI_API_KEY", "gpt-4o"),
        "openrouter": OpenAICompatBackend("openrouter", "https://openrouter.ai/api/v1/chat/completions",
                                          "OPENROUTER_API_KEY", "openai/gpt-4o"),
        "ollama":     OpenAICompatBackend("ollama", "http://localhost:11434/v1/chat/completions",
                                          "OLLAMA_API_KEY", os.environ.get("CC_COPILOT_MODEL", "llama3.2"),
                                          needs_key=False),
    }
    base = os.environ.get("CC_COPILOT_API_BASE", "").strip().rstrip("/")
    if base:
        if not base.endswith("/chat/completions"):
            base = base + "/chat/completions"
        reg["custom"] = OpenAICompatBackend(
            "custom", base, "CC_COPILOT_API_KEY",
            os.environ.get("CC_COPILOT_MODEL", "gpt-4o"),
            needs_key=bool(os.environ.get("CC_COPILOT_API_KEY")))
    return reg


def resolve(name: str = None) -> Backend:
    reg = registry()
    if name:
        if name in reg:
            return reg[name]
        raise BackendError(f"unknown backend {name!r}; known: {', '.join(sorted(reg))}")
    # A named default (flag-less) wins over the mere presence of a custom
    # endpoint — so CC_COPILOT_API_BASE can be configured without hijacking
    # whatever backend you actually picked as default.
    env = os.environ.get("CC_COPILOT_BACKEND", "").strip()
    if env:
        return resolve(env)
    if os.environ.get("CC_COPILOT_LLM_CMD", "").strip():
        return CliBackend("custom-cli", os.environ["CC_COPILOT_LLM_CMD"].split())
    if "custom" in reg:
        return reg["custom"]
    return reg["codex"]   # default backend: codex via ChatGPT OAuth
