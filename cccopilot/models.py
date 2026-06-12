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

The long-tail provider/model set is seeded from OpenClaw's provider catalog and
kept deliberately curated here. When a lineup moves, this table is the single
place to update.
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
        ModelInfo("mistralai/mistral-large-latest", "Mistral flagship"),
        ModelInfo("arcee/trinity-large-thinking", "reasoning"),
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
    "mistral": [
        ModelInfo("mistral-large-latest", "flagship — recommended"),
        ModelInfo("mistral-medium-3-5", "balanced"),
        ModelInfo("mistral-medium-2508", "pinned medium"),
        ModelInfo("mistral-small-latest", "fast + cheap"),
        ModelInfo("codestral-latest", "coding"),
        ModelInfo("devstral-medium-latest", "agentic coding"),
        ModelInfo("magistral-small", "reasoning"),
        ModelInfo("pixtral-large-latest", "vision"),
    ],
    "together": [
        ModelInfo("moonshotai/Kimi-K2.6", "Kimi flagship — recommended"),
        ModelInfo("meta-llama/Llama-3.3-70B-Instruct-Turbo", "open weights"),
        ModelInfo("deepseek-ai/DeepSeek-V4-Pro", "DeepSeek flagship"),
        ModelInfo("Qwen/Qwen2.5-7B-Instruct-Turbo", "small + fast"),
        ModelInfo("zai-org/GLM-5.1", "GLM flagship"),
    ],
    "fireworks": [
        ModelInfo("accounts/fireworks/models/kimi-k2p6", "Kimi flagship — recommended"),
        ModelInfo("accounts/fireworks/routers/kimi-k2p5-turbo", "Kimi turbo router"),
    ],
    "cerebras": [
        ModelInfo("zai-glm-4.7", "GLM — recommended"),
        ModelInfo("gpt-oss-120b", "open-weights flagship"),
        ModelInfo("qwen-3-235b-a22b-instruct-2507", "Qwen large instruct"),
        ModelInfo("llama3.1-8b", "small + fast"),
    ],
    "deepinfra": [
        ModelInfo("deepseek-ai/DeepSeek-V4-Flash", "DeepSeek fast — recommended"),
        ModelInfo("deepseek-ai/DeepSeek-V3.2", "DeepSeek stable"),
        ModelInfo("zai-org/GLM-5.1", "GLM flagship"),
        ModelInfo("stepfun-ai/Step-3.5-Flash", "StepFun fast"),
        ModelInfo("MiniMaxAI/MiniMax-M2.5", "MiniMax"),
        ModelInfo("moonshotai/Kimi-K2.5", "Kimi"),
        ModelInfo("nvidia/NVIDIA-Nemotron-3-Super-120B-A12B", "Nemotron"),
        ModelInfo("meta-llama/Llama-3.3-70B-Instruct-Turbo", "open weights"),
    ],
    "huggingface": [
        ModelInfo("deepseek-ai/DeepSeek-V3.1", "recommended"),
        ModelInfo("deepseek-ai/DeepSeek-R1", "reasoning"),
        ModelInfo("meta-llama/Llama-3.3-70B-Instruct-Turbo", "open weights"),
        ModelInfo("openai/gpt-oss-120b", "open-weights flagship"),
    ],
    "nvidia": [
        ModelInfo("nvidia/nemotron-3-ultra-550b-a55b", "flagship — recommended"),
        ModelInfo("nvidia/nemotron-3-super-120b-a12b", "smaller Nemotron"),
        ModelInfo("moonshotai/kimi-k2.5", "Kimi"),
        ModelInfo("minimaxai/minimax-m2.7", "MiniMax"),
        ModelInfo("minimaxai/minimax-m2.5", "MiniMax previous"),
        ModelInfo("z-ai/glm-5.1", "GLM flagship"),
        ModelInfo("z-ai/glm5", "GLM"),
    ],
    "chutes": [
        ModelInfo("zai-org/GLM-4.7-TEE", "TEE default"),
        ModelInfo("zai-org/GLM-5-TEE", "GLM TEE"),
        ModelInfo("deepseek-ai/DeepSeek-V3.2-TEE", "DeepSeek TEE"),
        ModelInfo("Qwen/Qwen3-Coder-Next-TEE", "coding TEE"),
        ModelInfo("moonshotai/Kimi-K2.5-TEE", "Kimi TEE"),
        ModelInfo("openai/gpt-oss-120b-TEE", "open-weights TEE"),
        ModelInfo("Qwen/Qwen3-235B-A22B-Instruct-2507-TEE", "Qwen large TEE"),
        ModelInfo("MiniMaxAI/MiniMax-M2.5-TEE", "MiniMax TEE"),
        ModelInfo("deepseek-ai/DeepSeek-R1-0528-TEE", "reasoning TEE"),
    ],
    "novita": [
        ModelInfo("moonshotai/kimi-k2.5", "Kimi — recommended"),
        ModelInfo("minimax/minimax-m2.7", "MiniMax"),
        ModelInfo("zai-org/glm-5", "GLM"),
        ModelInfo("deepseek/deepseek-v3-0324", "DeepSeek stable"),
        ModelInfo("deepseek/deepseek-r1-0528", "reasoning"),
        ModelInfo("qwen/qwen3-235b-a22b-fp8", "Qwen large"),
    ],
    "venice": [
        ModelInfo("kimi-k2-5", "Kimi — recommended"),
        ModelInfo("deepseek-v3.2", "DeepSeek"),
        ModelInfo("qwen3-235b-a22b-thinking-2507", "Qwen thinking"),
        ModelInfo("qwen3-235b-a22b-instruct-2507", "Qwen instruct"),
        ModelInfo("qwen3-coder-480b-a35b-instruct", "coding"),
        ModelInfo("qwen3-coder-480b-a35b-instruct-turbo", "coding turbo"),
        ModelInfo("qwen3-next-80b", "Qwen next"),
        ModelInfo("hermes-3-llama-3.1-405b", "Hermes"),
        ModelInfo("llama-3.3-70b", "open weights"),
        ModelInfo("llama-3.2-3b", "small"),
    ],
    "arcee": [
        ModelInfo("trinity-large-thinking", "reasoning — recommended"),
        ModelInfo("trinity-large-preview", "large preview"),
        ModelInfo("trinity-mini", "small + fast"),
    ],
    "gmi": [
        ModelInfo("google/gemini-3.1-flash-lite", "Gemini via GMI — recommended"),
        ModelInfo("zai-org/GLM-5.1-FP8", "GLM"),
        ModelInfo("deepseek-ai/DeepSeek-V3.2", "DeepSeek"),
        ModelInfo("moonshotai/Kimi-K2.5", "Kimi"),
        ModelInfo("anthropic/claude-sonnet-4.6", "Claude"),
        ModelInfo("openai/gpt-5.4", "OpenAI"),
    ],
    "stepfun": [
        ModelInfo("step-3.5-flash", "fast — recommended"),
        ModelInfo("step-3.5-flash-2603", "pinned snapshot"),
    ],
    "xiaomi": [
        ModelInfo("mimo-v2-flash", "fast — recommended"),
        ModelInfo("mimo-v2-pro", "higher quality"),
        ModelInfo("mimo-v2-omni", "multimodal"),
    ],
    "volcengine": [
        ModelInfo("doubao-seed-code-preview-251028", "coding — recommended"),
        ModelInfo("doubao-seed-1-8-251228", "Doubao"),
        ModelInfo("kimi-k2-5-260127", "Kimi"),
        ModelInfo("glm-4-7-251222", "GLM"),
        ModelInfo("deepseek-v3-2-251201", "DeepSeek"),
    ],
    "tencent-tokenhub": [
        ModelInfo("hy3-preview", "Hunyuan preview — recommended"),
    ],
    "ollama": [
        ModelInfo("llama3.2", "small, runs anywhere — default"),
        ModelInfo("qwen3", "strong local all-rounder"),
        ModelInfo("gemma3", "best single-GPU quality"),
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
    "mistral": "mistral",
    "mistralai": "mistral",
    "together": "together",
    "fireworks": "fireworks",
    "cerebras": "cerebras",
    "deepinfra": "deepinfra",
    "huggingface": "huggingface",
    "hf": "huggingface",
    "nvidia": "nvidia",
    "chutes": "chutes",
    "novita": "novita",
    "venice": "venice",
    "arcee": "arcee",
    "gmi": "gmi",
    "stepfun": "stepfun",
    "xiaomi": "xiaomi",
    "volcengine": "volcengine",
    "tencent-tokenhub": "tencent-tokenhub",
    "tencent": "tencent-tokenhub",
    "ollama": "ollama",
}

# Backends whose model ids commonly contain provider/model slashes. While one of
# these is active, a typed slash id should stay on the current backend unless the
# user uses the explicit backend:model form handled by the caller.
_ROUTER_MODEL_BACKENDS = {
    "openrouter",
    "groq",
    "together",
    "fireworks",
    "deepinfra",
    "huggingface",
    "nvidia",
    "chutes",
    "novita",
    "gmi",
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
