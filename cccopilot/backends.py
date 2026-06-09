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

Both shapes also expose ``stream()`` (incremental chunks; claude speaks
stream-json, codex ``--json`` JSONL, HTTP backends SSE — anything else falls
back to one blocking chunk) and set ``last_usage`` to the provider's exact
:class:`Usage` when reported.

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
import threading
import urllib.error
import urllib.request


class BackendError(RuntimeError):
    pass


class Usage:
    """Exact token usage reported by a backend for one completion.

    ``exact`` distinguishes provider-reported numbers from local estimates so
    HUD surfaces can drop the ``~``. ``cost_usd`` is only known where the
    provider reports it (the claude CLI's result event)."""

    __slots__ = ("input_tokens", "output_tokens", "cached_tokens", "cost_usd", "exact")

    def __init__(self, input_tokens=0, output_tokens=0, cached_tokens=0,
                 cost_usd=None, exact=True):
        self.input_tokens = int(input_tokens or 0)
        self.output_tokens = int(output_tokens or 0)
        self.cached_tokens = int(cached_tokens or 0)
        self.cost_usd = cost_usd
        self.exact = bool(exact)

    def as_dict(self) -> dict:
        d = {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
             "cached_tokens": self.cached_tokens, "exact": self.exact}
        if self.cost_usd is not None:
            d["cost_usd"] = self.cost_usd
        return d


class Backend:
    name = "?"
    # exact usage for the LAST complete()/stream() call, when the provider
    # reports it (None otherwise). Instances are built fresh per call by
    # registry()/resolve(), so this is per-call state in practice.
    last_usage = None

    def available(self) -> bool:
        return False

    def reason(self) -> str:
        """Why it's unavailable (for a helpful message)."""
        return ""

    def complete(self, prompt: str, model: str = None, timeout: int = 180) -> str:
        raise NotImplementedError

    def stream(self, prompt: str, model: str = None, timeout: int = 180):
        """Yield the answer incrementally. Base fallback: one blocking chunk.

        The concatenated chunks ARE the full answer; ``last_usage`` is set by
        the time iteration finishes (or stays None). Errors raise BackendError
        during iteration — consumers must wrap the loop, not just the call."""
        yield self.complete(prompt, model=model, timeout=timeout)

    def describe(self) -> str:
        return self.name


# ── stream-event parsers (pure: text lines in, events out — unit-testable) ─

def _parse_claude_stream(lines):
    """Fold `claude -p --output-format stream-json --verbose
    --include-partial-messages` JSONL into ("text", chunk) / ("usage", Usage)
    events. Token deltas are the primary feed; if the CLI emitted none (older
    builds without partial messages), fall back to the per-message `assistant`
    events, then to the final result text."""
    saw_delta = False
    fallback_texts = []
    result_text = ""
    usage = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue                      # stray non-JSON noise on stdout
        t = obj.get("type")
        if t == "stream_event":
            ev = obj.get("event") or {}
            delta = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                txt = delta.get("text") or ""
                if txt:
                    saw_delta = True
                    yield ("text", txt)
        elif t == "assistant" and not saw_delta:
            msg = obj.get("message") or {}
            parts = [c.get("text", "") for c in (msg.get("content") or [])
                     if isinstance(c, dict) and c.get("type") == "text"]
            txt = "".join(parts)
            if txt:
                fallback_texts.append(txt)
        elif t == "result":
            u = obj.get("usage") or {}
            if u:
                cache_read = u.get("cache_read_input_tokens", 0) or 0
                cache_make = u.get("cache_creation_input_tokens", 0) or 0
                usage = Usage(
                    input_tokens=(u.get("input_tokens", 0) or 0) + cache_read + cache_make,
                    output_tokens=u.get("output_tokens", 0) or 0,
                    cached_tokens=cache_read,
                    cost_usd=obj.get("total_cost_usd"))
            result_text = obj.get("result") or ""
            if obj.get("is_error"):
                if usage:
                    yield ("usage", usage)
                raise BackendError(result_text or "claude reported an error")
    if not saw_delta:
        txt = "\n\n".join(fallback_texts) or result_text
        if txt:
            yield ("text", txt)
    if usage:
        yield ("usage", usage)


def _parse_codex_stream(lines):
    """Fold `codex exec --json` JSONL into ("text", chunk) / ("usage", Usage)
    events. Codex emits agent messages whole (no deltas in 0.137); usage comes
    from the turn.completed event (token counts only, no cost)."""
    first = True
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        t = obj.get("type") or ""
        if t == "item.completed":
            item = obj.get("item") or {}
            if item.get("type") == "agent_message":
                txt = item.get("text") or ""
                if txt:
                    yield ("text", ("" if first else "\n\n") + txt)
                    first = False
        elif t == "turn.completed":
            u = obj.get("usage") or {}
            if u:
                yield ("usage", Usage(
                    input_tokens=u.get("input_tokens", 0) or 0,
                    output_tokens=u.get("output_tokens", 0) or 0,
                    cached_tokens=u.get("cached_input_tokens", 0) or 0))
        elif t in ("turn.failed", "error"):
            msg = (obj.get("error") or {}).get("message") if isinstance(obj.get("error"), dict) \
                else obj.get("message") or obj.get("error")
            raise BackendError(str(msg or "codex turn failed"))


def _parse_sse_stream(lines):
    """Fold an OpenAI-compatible SSE body into ("text", chunk) / ("usage",
    Usage) events. Tolerates a server that ignored `stream: true` and sent one
    plain JSON body (yielded whole at the end)."""
    non_sse = []
    saw_sse = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not line.startswith("data:"):
            if not saw_sse:
                non_sse.append(line)
            continue
        saw_sse = True
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        for ch in (obj.get("choices") or []):
            txt = ((ch.get("delta") or {}).get("content")) or ""
            if txt:
                yield ("text", txt)
        u = obj.get("usage")
        if u:
            details = u.get("prompt_tokens_details") or {}
            yield ("usage", Usage(
                input_tokens=u.get("prompt_tokens", 0) or 0,
                output_tokens=u.get("completion_tokens", 0) or 0,
                cached_tokens=details.get("cached_tokens", 0) or 0))
    if not saw_sse and non_sse:
        # plain JSON response despite stream:true — extract the blocking shape
        try:
            obj = json.loads("\n".join(non_sse))
            txt = obj["choices"][0]["message"]["content"]
            if txt:
                yield ("text", txt)
            u = obj.get("usage")
            if u:
                yield ("usage", Usage(
                    input_tokens=u.get("prompt_tokens", 0) or 0,
                    output_tokens=u.get("completion_tokens", 0) or 0))
        except (ValueError, KeyError, IndexError, TypeError):
            pass


# ── CLI capability probe (one `--help` per binary per process) ────────────

_HELP_CACHE = {}


def _cli_help(argv) -> str:
    key = tuple(argv)
    if key in _HELP_CACHE:
        return _HELP_CACHE[key]
    try:
        p = subprocess.run(list(argv) + ["--help"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15,
                           stdin=subprocess.DEVNULL)
        out = (p.stdout or "") + "\n" + (p.stderr or "")
    except Exception:
        out = ""
    _HELP_CACHE[key] = out
    return out


# ── CLI backends ─────────────────────────────────────────────────────────

class CliBackend(Backend):
    def __init__(self, name, argv, model_args=None, cwd=None, flavor=None):
        self.name = name
        self.argv = [a for a in argv if a]
        # model_args: callable(model)->list[str], inserted before the prompt
        self.model_args = model_args
        # cwd: run the CLI here. For agent CLIs (claude/codex) that log a
        # session transcript per call, a neutral dir keeps those out of the
        # user's project session list — and narration wants no repo context.
        self.cwd = cwd
        # flavor: which native JSONL stream dialect the CLI speaks ("claude" /
        # "codex"); None = no native streaming, stream() falls back to complete().
        self.flavor = flavor

    def _bin(self):
        return self.argv[0] if self.argv else ""

    def available(self) -> bool:
        b = self._bin()
        return bool(b) and (os.path.isfile(b) or shutil.which(b) is not None)

    def reason(self) -> str:
        return f"`{self._bin()}` not found on PATH"

    def _full_argv(self, prompt, model, extra=None):
        argv = list(self.argv) + list(extra or [])
        if model and self.model_args:
            argv += list(self.model_args(model))
        return argv + [prompt]

    def complete(self, prompt, model=None, timeout=180) -> str:
        self.last_usage = None
        try:
            # pin UTF-8 (don't trust the locale): the prompt and the model's
            # reply routinely carry CJK / emoji / accented text, and a C/POSIX
            # locale would otherwise decode stdout as ASCII and mangle or crash.
            # stdin=DEVNULL: codex exec treats a piped stdin as extra prompt
            # input ("Reading additional input from stdin...") and would hang.
            p = subprocess.run(self._full_argv(prompt, model),
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=self.cwd,
                               stdin=subprocess.DEVNULL)
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

    # which extra flags switch the CLI into its JSONL stream mode, gated on
    # the installed binary actually advertising them (one cached --help probe)
    def _stream_args(self):
        if self.flavor == "claude":
            help_text = _cli_help(self.argv)        # `claude -p --help`
            if "stream-json" in help_text:
                extra = ["--output-format", "stream-json", "--verbose"]
                if "--include-partial-messages" in help_text:
                    extra.append("--include-partial-messages")
                return extra, _parse_claude_stream
        elif self.flavor == "codex":
            if "--json" in _cli_help(self.argv):    # `codex exec … --help`
                return ["--json"], _parse_codex_stream
        return None, None

    def stream(self, prompt, model=None, timeout=180):
        extra, parser = self._stream_args()
        if parser is None:
            yield self.complete(prompt, model=model, timeout=timeout)
            return
        self.last_usage = None
        try:
            p = subprocess.Popen(self._full_argv(prompt, model, extra),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 stdin=subprocess.DEVNULL, text=True,
                                 encoding="utf-8", errors="replace", cwd=self.cwd)
        except FileNotFoundError:
            raise BackendError(self.reason())
        # drain stderr off-thread (a full pipe would deadlock the child)
        err_chunks = []
        t_err = threading.Thread(target=lambda: err_chunks.append(p.stderr.read()),
                                 daemon=True)
        t_err.start()
        timed_out = threading.Event()

        def _kill():
            timed_out.set()
            try:
                p.kill()
            except Exception:
                pass

        watchdog = threading.Timer(timeout, _kill)
        watchdog.daemon = True
        watchdog.start()
        got = False
        try:
            for kind, val in parser(iter(p.stdout.readline, "")):
                if kind == "text" and val:
                    got = True
                    yield val
                elif kind == "usage":
                    self.last_usage = val
            rc = p.wait()
            if timed_out.is_set():
                raise BackendError(f"{self.name} timed out after {timeout}s"
                                   + (" (partial answer shown)" if got else ""))
            if rc != 0:
                err = (err_chunks[0].strip() if err_chunks and err_chunks[0] else "")
                raise BackendError((err or f"{self.name} exited {rc}")
                                   + (" (partial answer shown)" if got else ""))
            if not got:
                raise BackendError(f"{self.name} returned no output")
        finally:
            watchdog.cancel()
            try:
                p.kill()      # no-op if already exited
                p.wait(timeout=5)      # reap (also after an abandoned stream)
            except Exception:
                pass

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

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        key = self._key()
        if self.needs_key and not key:
            raise BackendError(f"set {self.key_env} to use the {self.name} backend")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def complete(self, prompt, model=None, timeout=180) -> str:
        self.last_usage = None
        headers = self._headers()
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
        u = data.get("usage") if isinstance(data, dict) else None
        if u:
            self.last_usage = Usage(
                input_tokens=u.get("prompt_tokens", 0) or 0,
                output_tokens=u.get("completion_tokens", 0) or 0,
                cached_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            raise BackendError(f"{self.name} unexpected response: "
                               f"{json.dumps(data, ensure_ascii=False)[:200]}")

    def stream(self, prompt, model=None, timeout=180):
        self.last_usage = None
        headers = self._headers()
        base = {"model": model or self.default_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True}
        # first attempt asks for the exact-usage final chunk; some servers
        # reject the stream_options field (HTTP 400/422) — retry once without.
        attempts = [dict(base, stream_options={"include_usage": True}), base]
        resp = None
        for i, body in enumerate(attempts):
            req = urllib.request.Request(self.endpoint,
                                         data=json.dumps(body).encode("utf-8"),
                                         method="POST", headers=headers)
            try:
                resp = urllib.request.urlopen(req, timeout=timeout)
                break
            except urllib.error.HTTPError as e:
                if i == 0 and e.code in (400, 422):
                    continue
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
        if resp is None:
            raise BackendError(f"{self.name} rejected the streaming request")
        got = False
        try:
            # http.client decodes chunked transfer transparently; iterating the
            # response yields SSE lines as they arrive. The urlopen timeout is
            # per-socket-read, so a stalled stream raises mid-iteration.
            def _lines():
                for raw in resp:
                    yield raw.decode("utf-8", "replace")
            for kind, val in _parse_sse_stream(_lines()):
                if kind == "text" and val:
                    got = True
                    yield val
                elif kind == "usage":
                    self.last_usage = val
        except (TimeoutError, OSError) as e:
            raise BackendError(f"{self.name} stream stalled: {e}"
                               + (" (partial answer shown)" if got else ""))
        finally:
            try:
                resp.close()
            except Exception:
                pass
        if not got:
            raise BackendError(f"{self.name} returned no output")

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
                             cwd=tempfile.gettempdir(), flavor="claude"),
        # --skip-git-repo-check: we run codex in a neutral temp dir (not the
        # watched repo), so it must not insist on being inside a git project.
        "codex":  CliBackend("codex", [shutil.which("codex") or "codex", "exec",
                                       "--skip-git-repo-check"],
                             model_args=lambda m: ["-c", f"model={m}"],
                             cwd=tempfile.gettempdir(), flavor="codex"),
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
