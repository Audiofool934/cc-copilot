"""Curated per-provider model catalog (June 2026).

One compact table — the same data shape OpenClaw uses (id + a one-line note per
model), at cc-copilot scale. The FIRST entry of each list is the provider's
recommended default; the rest are offered by the ``/model`` picker and the
``cc-copilot init`` wizard. The catalog is a convenience, never a restriction:
every surface that offers these also accepts a free-form model id (``--model``,
``model = "..."`` in the config, or the picker's "custom…" row).

Only API (OpenAI-compatible) backends are cataloged. CLI backends (claude /
codex / gemini / llm) deliberately have no catalog: the cockpit keeps their
model unset so the agent CLI's own default applies (see the
claude→deepseek→claude stale-model bug, CHANGELOG 0.14.1); power users can
still pass ``--model``.

The provider/model set is deliberately compact. Less common providers are best
reached through OpenRouter or a custom OpenAI-compatible endpoint.
"""

from __future__ import annotations


class ModelInfo:
    __slots__ = ("id", "note")

    def __init__(self, id, note=""):
        self.id = id
        self.note = note


# provider name (== backends.registry() key) → ordered models, first = default
CATALOG = {
    "deepseek": [
        ModelInfo("deepseek-v4-flash", "fast + cheap, 1M ctx — recommended"),
        ModelInfo("deepseek-v4-pro", "flagship, 1M ctx"),
        ModelInfo("deepseek-chat", "legacy — deprecated 2026-07-24"),
        ModelInfo("deepseek-reasoner", "legacy thinking — deprecated 2026-07-24"),
    ],
    "openai": [
        ModelInfo("gpt-5.4", "affordable all-rounder — recommended"),
        ModelInfo("gpt-5.5", "flagship"),
        ModelInfo("gpt-5.4-mini", "low latency / low cost"),
        ModelInfo("gpt-5.4-nano", "cheapest"),
    ],
    "openrouter": [
        ModelInfo("openai/gpt-5.4-mini", "cheap default"),
        ModelInfo("openrouter/auto", "OpenRouter-selected model"),
        ModelInfo("openai/gpt-5.5", "OpenAI flagship via OpenRouter"),
        ModelInfo("anthropic/claude-sonnet-4.6", "strong all-rounder"),
        ModelInfo("anthropic/claude-haiku-4.5", "fast + cheap"),
        ModelInfo("google/gemini-3.1-flash-lite", "fast, huge ctx"),
        ModelInfo("moonshotai/kimi-k2.6", "Kimi flagship"),
        ModelInfo("moonshotai/kimi-k2.5", "Kimi previous generation"),
        ModelInfo("deepseek/deepseek-v4-flash", "very cheap"),
        ModelInfo("meta-llama/llama-3.3-70b-instruct", "open weights"),
    ],
    "moonshot": [
        ModelInfo("kimi-k2.6", "flagship, 256k ctx — recommended"),
        ModelInfo("kimi-k2.5", "previous generation"),
    ],
    "zai": [
        ModelInfo("glm-5.1", "flagship — recommended"),
        ModelInfo("glm-5", "general purpose"),
        ModelInfo("glm-5-turbo", "fast"),
        ModelInfo("glm-5v-turbo", "vision"),
        ModelInfo("glm-4.7", "previous flagship"),
        ModelInfo("glm-4.7-flash", "cheap / free tier"),
        ModelInfo("glm-4.7-flashx", "extra fast"),
        ModelInfo("glm-4.6", "legacy stable"),
    ],
    "qwen": [
        ModelInfo("qwen3-max", "flagship — recommended"),
        ModelInfo("qwen3-max-2026-01-23", "pinned flagship snapshot"),
        ModelInfo("qwen3.6-plus", "latest plus line"),
        ModelInfo("qwen3.5-plus", "balanced, 1M ctx"),
        ModelInfo("qwen3.5-flash", "fast + cheap"),
        ModelInfo("qwen3-coder-plus", "coding"),
        ModelInfo("qwen3-coder-next", "coding preview"),
        ModelInfo("MiniMax-M2.5", "third-party via DashScope"),
        ModelInfo("glm-5", "third-party via DashScope"),
        ModelInfo("glm-4.7", "third-party via DashScope"),
        ModelInfo("kimi-k2.5", "third-party via DashScope"),
    ],
    "groq": [
        ModelInfo("llama-3.3-70b-versatile", "recommended"),
        ModelInfo("llama-3.1-8b-instant", "fastest"),
        ModelInfo("openai/gpt-oss-120b", "open-weights flagship"),
    ],
    "xai": [
        ModelInfo("grok-4.3", "flagship — recommended"),
        ModelInfo("grok-4.20", "latest Grok 4 line"),
        ModelInfo("grok-4-fast", "fast"),
        ModelInfo("grok-4-1-fast", "fast pinned generation"),
        ModelInfo("grok-4", "stable"),
        ModelInfo("grok-build-0.1", "cost-optimized"),
    ],
    "gemini-api": [
        ModelInfo("gemini-3.5-flash", "stable + fast — recommended"),
        ModelInfo("gemini-3.1-pro-preview", "flagship (preview)"),
        ModelInfo("gemini-3.1-flash-lite", "budget"),
        ModelInfo("gemini-3-flash-preview", "preview"),
        ModelInfo("gemini-2.5-pro", "previous flagship"),
        ModelInfo("gemini-2.5-flash", "previous fast"),
        ModelInfo("gemini-2.5-flash-lite", "previous budget"),
    ],
    "ollama": [
        ModelInfo("llama3.2", "small, runs anywhere — default"),
        ModelInfo("qwen3", "strong local all-rounder"),
        ModelInfo("gemma3", "best single-GPU quality"),
    ],
    "ollama-cloud": [
        ModelInfo("glm-5.2", "flagship general — recommended; browse more at ollama.com/search?c=cloud"),
        ModelInfo("deepseek-v4-pro", "flagship reasoning"),
        ModelInfo("deepseek-v4-flash", "cheap and fast"),
        ModelInfo("kimi-k2.7-code", "coding"),
        ModelInfo("gemma4:31b", "Google open model"),
    ],
}


_REF_BACKEND_ALIASES = {
    "openai": "openai",
    "deepseek": "deepseek",
    "openrouter": "openrouter",
    "moonshot": "moonshot",
    "moonshotai": "moonshot",
    "zai": "zai",
    "z-ai": "zai",
    "zai-org": "zai",
    "qwen": "qwen",
    "dashscope": "qwen",
    "modelstudio": "qwen",
    "groq": "groq",
    "xai": "xai",
    "x-ai": "xai",
    "google": "gemini-api",
    "gemini": "gemini-api",
    "gemini-api": "gemini-api",
    "ollama": "ollama",
    "ollama-cloud": "ollama-cloud",
}

# Backends whose model ids commonly contain provider/model slashes. While one of
# these is active, a typed slash id should stay on the current backend unless the
# user uses the explicit backend:model form handled by the caller.
_ROUTER_MODEL_BACKENDS = {
    "openrouter",
    "groq",
}


def models_for(backend_name: str):
    """The curated models for a backend (possibly empty — CLI backends)."""
    return list(CATALOG.get((backend_name or "").strip().lower(), ()))


def default_for(backend_name: str) -> str:
    """The recommended default model id, or "" when uncataloged."""
    ms = CATALOG.get((backend_name or "").strip().lower())
    return ms[0].id if ms else ""


def find(backend_name: str, model_id: str):
    """The catalog entry for an exact model id under a backend, or None."""
    want = (model_id or "").strip()
    for m in CATALOG.get((backend_name or "").strip().lower(), ()):
        if m.id == want:
            return m
    return None


def resolve_ref(model_ref: str, current_backend: str = ""):
    """Resolve a provider/model shorthand into ``(backend, model)``.

    ``/model openai/gpt-5.5`` and ``/model google/gemini-3.1-flash-lite`` are
    convenient aliases for switching backend and model together. Exact model ids
    for the current backend still win first, so OpenRouter ids such as
    ``anthropic/claude-sonnet-4.6`` do not get misread as provider switches.
    Returns None when ``model_ref`` is just a free-form model id.
    """
    raw = (model_ref or "").strip()
    if "/" not in raw:
        return None
    cur = (current_backend or "").strip().lower()
    if cur and find(cur, raw):
        return (cur, raw)
    provider, _, model = raw.partition("/")
    backend = _REF_BACKEND_ALIASES.get(provider.strip().lower())
    if not backend or not model.strip():
        return None
    if backend == cur:
        if find(cur, raw):
            return (cur, raw)
        return (cur, model.strip())
    if cur in _ROUTER_MODEL_BACKENDS:
        return (cur, raw)
    if find(backend, raw):
        return (backend, raw)
    return (backend, model.strip())
