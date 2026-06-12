"""The curated model catalog and the surfaces that read it."""

import os
import unittest

from cccopilot import backends as BK, models as M, onboard as OB


class TestCatalog(unittest.TestCase):
    def test_deepseek_default_is_v4_flash(self):
        # deepseek-chat is deprecated 2026-07-24; the default must be the v4 line
        self.assertEqual(M.default_for("deepseek"), "deepseek-v4-flash")
        ids = [mi.id for mi in M.models_for("deepseek")]
        self.assertIn("deepseek-v4-pro", ids)
        self.assertIn("deepseek-chat", ids)              # still selectable…
        legacy = M.find("deepseek", "deepseek-chat")
        self.assertIn("deprecated", legacy.note)         # …but clearly marked

    def test_every_catalog_backend_exists_in_registry(self):
        reg = BK.registry()
        for name in M.CATALOG:
            self.assertIn(name, reg, name)
            be = reg[name]
            self.assertIsInstance(be, BK.OpenAICompatBackend, name)
            # the registry default IS the catalog default — one source of truth
            self.assertEqual(be.default_model, M.default_for(name), name)

    def test_cli_backends_have_no_catalog(self):
        # CLI backends keep model=None semantics; no catalog rows for them
        for name in ("claude", "codex", "gemini", "llm"):
            self.assertEqual(M.models_for(name), [], name)

    def test_find_and_unknown(self):
        self.assertIsNone(M.find("deepseek", "nope"))
        self.assertIsNone(M.find("not-a-backend", "deepseek-v4-flash"))
        self.assertEqual(M.models_for("not-a-backend"), [])
        self.assertEqual(M.default_for("not-a-backend"), "")


class TestNewProviders(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("DASHSCOPE_API_BASE",)}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_providers_present_with_key_envs(self):
        reg = BK.registry()
        expect = {
            "moonshot": "MOONSHOT_API_KEY",
            "zai": "ZAI_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
            "groq": "GROQ_API_KEY",
            "xai": "XAI_API_KEY",
            "gemini-api": "GEMINI_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "together": "TOGETHER_API_KEY",
            "fireworks": "FIREWORKS_API_KEY",
            "cerebras": "CEREBRAS_API_KEY",
            "deepinfra": "DEEPINFRA_API_KEY",
            "huggingface": "HUGGINGFACE_API_KEY",
            "nvidia": "NVIDIA_API_KEY",
            "chutes": "CHUTES_API_KEY",
            "novita": "NOVITA_API_KEY",
            "venice": "VENICE_API_KEY",
            "arcee": "ARCEE_API_KEY",
            "gmi": "GMI_API_KEY",
            "stepfun": "STEPFUN_API_KEY",
            "xiaomi": "XIAOMI_API_KEY",
            "volcengine": "VOLCENGINE_API_KEY",
            "tencent-tokenhub": "TENCENT_TOKENHUB_API_KEY",
        }
        for name, key_env in expect.items():
            self.assertIn(name, reg)
            self.assertEqual(reg[name].key_env, key_env, name)

    def test_provider_refs_resolve(self):
        self.assertEqual(M.resolve_ref("openai/gpt-5.5"),
                         ("openai", "gpt-5.5"))
        self.assertEqual(M.resolve_ref("google/gemini-3.1-flash-lite"),
                         ("gemini-api", "gemini-3.1-flash-lite"))
        self.assertEqual(M.resolve_ref("openrouter/moonshotai/kimi-k2.6"),
                         ("openrouter", "moonshotai/kimi-k2.6"))
        self.assertEqual(M.resolve_ref("anthropic/claude-sonnet-4.6",
                                       current_backend="openrouter"),
                         ("openrouter", "anthropic/claude-sonnet-4.6"))
        self.assertEqual(M.resolve_ref("google/gemini-3.1-flash-lite",
                                       current_backend="openrouter"),
                         ("openrouter", "google/gemini-3.1-flash-lite"))
        self.assertEqual(M.resolve_ref("openai/gpt-6-preview",
                                       current_backend="openrouter"),
                         ("openrouter", "openai/gpt-6-preview"))
        self.assertEqual(M.resolve_ref("openrouter/openai/gpt-5.5",
                                       current_backend="openrouter"),
                         ("openrouter", "openai/gpt-5.5"))
        self.assertIsNone(M.resolve_ref("meta-llama/llama-3.1-405b-instruct:free",
                                        current_backend="deepseek"))
        self.assertIsNone(M.resolve_ref("not/a-ref"))

    def test_gemini_api_distinct_from_gemini_cli(self):
        reg = BK.registry()
        self.assertIsInstance(reg["gemini"], BK.CliBackend)
        self.assertIsInstance(reg["gemini-api"], BK.OpenAICompatBackend)
        # the OpenAI-compat path ends /openai/ — no /v1 in between
        self.assertIn("/v1beta/openai/chat/completions", reg["gemini-api"].endpoint)

    def test_qwen_endpoint_override(self):
        self.assertIn("dashscope-intl", BK.registry()["qwen"].endpoint)
        os.environ["DASHSCOPE_API_BASE"] = \
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.assertEqual(
            BK.registry()["qwen"].endpoint,
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")

    def test_ollama_default_no_longer_reads_cc_copilot_model(self):
        # regression: a model picked for ANY other provider used to leak into
        # ollama's default via the CC_COPILOT_MODEL export
        os.environ["CC_COPILOT_MODEL"] = "gpt-5.5"
        try:
            self.assertEqual(BK.registry()["ollama"].default_model,
                             M.default_for("ollama"))
        finally:
            os.environ.pop("CC_COPILOT_MODEL", None)


class TestOnboardChoices(unittest.TestCase):
    def test_every_api_provider_has_a_choice(self):
        # the inline key-prompt on /model switches keys off choice_for_or_none —
        # a cataloged API provider without a Choice would switch silently and
        # fail at call time (the v0.14.1 bug, regressed for new providers)
        for name in M.CATALOG:
            if name == "ollama":                          # keyless, power-user
                continue
            c = OB.choice_for_or_none(name)
            self.assertIsNotNone(c, name)
            self.assertEqual(c.kind, "api", name)
            self.assertEqual(c.default_model, M.default_for(name), name)

    def test_featured_subset_stays_compact(self):
        full = OB.detect()
        featured = OB.detect(featured_only=True)
        self.assertGreater(len(full), len(featured))
        labels = [d.choice.label for d in featured]
        for must in ("Claude", "Codex", "OpenAI", "DeepSeek", "OpenRouter",
                     "Skip for now"):
            self.assertIn(must, labels)
        self.assertEqual(len(featured), 6)                # the modal's height budget


if __name__ == "__main__":
    unittest.main()
