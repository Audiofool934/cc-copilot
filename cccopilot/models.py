"""Curated per-provider model catalog (June 2026).

One small, hand-checked table — the data shape OpenClaw uses (id + a one-line
note per model), at cc-copilot scale. The FIRST entry of each list is the
provider's recommended default; the rest are offered by the ``/model`` picker
and the ``cc-copilot init`` wizard. The catalog is a convenience, never a
restriction: every surface that offers these also accepts a free-form model id
(``--model``, ``model = "..."`` in the config, or the picker's "custom…" row).

Only API (OpenAI-compatible) backends are cataloged. CLI backends (claude /
codex / gemini / llm) deliberately have no catalog: the cockpit keeps their
model unset so the agent CLI's own default applies (see the
claude→deepseek→claude stale-model bug, CHANGELOG 0.14.1); power users can
still pass ``--model``.

Every id below was verified against the provider's live docs on 2026-06-10 —
when a lineup moves, this table is the single place to update.
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
        ModelInfo("anthropic/claude-sonnet-4.6", "strong all-rounder"),
        ModelInfo("anthropic/claude-haiku-4.5", "fast + cheap"),
        ModelInfo("google/gemini-3.5-flash", "fast, huge ctx"),
        ModelInfo("deepseek/deepseek-v4-flash", "very cheap"),
    ],
    "moonshot": [
        ModelInfo("kimi-k2.6", "flagship, 256k ctx — recommended"),
        ModelInfo("kimi-k2.5", "previous generation"),
    ],
    "zai": [
        ModelInfo("glm-5.1", "flagship — recommended"),
        ModelInfo("glm-5-turbo", "fast"),
        ModelInfo("glm-4.7-flash", "cheap / free tier"),
    ],
    "qwen": [
        ModelInfo("qwen3-max", "flagship — recommended"),
        ModelInfo("qwen3.5-plus", "balanced, 1M ctx"),
        ModelInfo("qwen3.5-flash", "fast + cheap"),
    ],
    "groq": [
        ModelInfo("llama-3.3-70b-versatile", "recommended"),
        ModelInfo("llama-3.1-8b-instant", "fastest"),
        ModelInfo("openai/gpt-oss-120b", "open-weights flagship"),
    ],
    "xai": [
        ModelInfo("grok-4.3", "flagship — recommended"),
        ModelInfo("grok-build-0.1", "cost-optimized"),
    ],
    "gemini-api": [
        ModelInfo("gemini-3.5-flash", "stable + fast — recommended"),
        ModelInfo("gemini-3.1-pro-preview", "flagship (preview)"),
        ModelInfo("gemini-3.1-flash-lite", "budget"),
    ],
    "ollama": [
        ModelInfo("llama3.2", "small, runs anywhere — default"),
        ModelInfo("qwen3", "strong local all-rounder"),
        ModelInfo("gemma3", "best single-GPU quality"),
    ],
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
