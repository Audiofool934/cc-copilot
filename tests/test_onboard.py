import os
import stat
import tempfile
import unittest

from cccopilot import onboard as OB
from cccopilot import config as CFG


_VARS = ("CC_COPILOT_CONFIG", "CC_COPILOT_NO_ONBOARD", "CC_COPILOT_BACKEND",
         "CC_COPILOT_MODEL", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
         "OPENROUTER_API_KEY", "MOONSHOT_API_KEY", "ZAI_API_KEY",
         "DASHSCOPE_API_KEY", "DASHSCOPE_API_BASE", "GROQ_API_KEY",
         "XAI_API_KEY", "GEMINI_API_KEY", "OLLAMA_API_KEY")


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in _VARS}
        self.dir = tempfile.mkdtemp()
        self.p = os.path.join(self.dir, "cc.toml")
        os.environ["CC_COPILOT_CONFIG"] = self.p
        # onboarding is globally disabled for the suite (tests/__init__); the
        # onboarding tests are the ones place that genuinely needs it enabled.
        os.environ.pop("CC_COPILOT_NO_ONBOARD", None)

    def tearDown(self):
        for k in _VARS:
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v


class TestNeedsOnboarding(_Base):
    def test_true_when_no_config(self):
        self.assertTrue(OB.needs_onboarding())

    def test_false_once_a_config_exists(self):
        OB.write_choice("skip")
        self.assertFalse(OB.needs_onboarding())

    def test_false_when_opted_out(self):
        os.environ["CC_COPILOT_NO_ONBOARD"] = "1"
        self.assertFalse(OB.needs_onboarding())

    def test_opt_out_falsey_values_still_onboard(self):
        for v in ("", "0", "false", "no", "off"):
            os.environ["CC_COPILOT_NO_ONBOARD"] = v
            self.assertTrue(OB.needs_onboarding(), v)


class TestDetect(_Base):
    def test_skip_always_ready(self):
        det = {d.choice.name or "skip": d for d in OB.detect()}
        self.assertTrue(det["skip"].ready)

    def test_api_choice_ready_only_with_key(self):
        det = {d.choice.name: d for d in OB.detect() if d.choice.name}
        self.assertFalse(det["openai"].ready)        # no key yet
        os.environ["OPENAI_API_KEY"] = "sk-xyz"
        det = {d.choice.name: d for d in OB.detect() if d.choice.name}
        self.assertTrue(det["openai"].ready)         # key present → ready
        self.assertIn("OPENAI_API_KEY", det["openai"].status)

    def test_every_curated_choice_present_and_ordered(self):
        labels = [d.choice.label for d in OB.detect()]
        self.assertEqual(labels, [
            "Claude", "Codex", "DeepSeek", "Gemini API", "Groq",
            "Moonshot Kimi", "OpenAI", "OpenRouter", "Qwen (DashScope)",
            "xAI Grok", "Z.ai GLM", "Ollama Cloud", "Skip for now",
        ])

    def test_key_provider_brand_colors(self):
        self.assertEqual(OB.choice_for("claude").brand_hex, "#cb7d5b")
        self.assertEqual(OB.choice_for("codex").brand_hex, "#347ff2")
        self.assertEqual(OB.choice_for("deepseek").brand_hex, "#8b5cf6")


class TestWriteChoice(_Base):
    def test_api_choice_writes_backend_model_and_key(self):
        OB.write_choice("openai", model="gpt-4o", key_value="sk-secret")
        data = CFG._load_simple(self.p)
        self.assertEqual(data.get("backend"), "openai")
        self.assertEqual(data.get("model"), "gpt-4o")
        self.assertEqual(data["env"]["OPENAI_API_KEY"], "sk-secret")

    def test_ollama_cloud_key_capture_persists_and_applies(self):
        # mirrors the cockpit's /model -> KeyPrompt -> _finish_api_switch path:
        # write_choice persists backend+model+key (0600), apply_to_env makes the
        # running process pick it up immediately.
        OB.write_choice("ollama-cloud", model="glm-5.2", key_value="oll-secret")
        data = CFG._load_simple(self.p)
        self.assertEqual(data.get("backend"), "ollama-cloud")
        self.assertEqual(data.get("model"), "glm-5.2")
        self.assertEqual(data["env"]["OLLAMA_API_KEY"], "oll-secret")
        OB.apply_to_env("ollama-cloud", model="glm-5.2", key_value="oll-secret")
        self.assertEqual(os.environ.get("CC_COPILOT_BACKEND"), "ollama-cloud")
        self.assertEqual(os.environ.get("OLLAMA_API_KEY"), "oll-secret")
        self.assertEqual(os.environ.get("CC_COPILOT_MODEL"), "glm-5.2")

    def test_skip_comments_out_backend(self):
        OB.write_choice("skip")
        data = CFG._load_simple(self.p)
        self.assertIsNone(data.get("backend"))       # commented, not active
        self.assertTrue(os.path.isfile(self.p))

    def test_file_is_chmod_600(self):
        OB.write_choice("claude")
        mode = stat.S_IMODE(os.stat(self.p).st_mode)
        self.assertEqual(mode, 0o600)

    def test_rerun_preserves_other_provider_keys_and_history(self):
        # a config the user already curated: two provider keys + history off
        with open(self.p, "w") as f:
            f.write('backend = "openai"\n[env]\n'
                    'OPENAI_API_KEY = "sk-openai"\nDEEPSEEK_API_KEY = "sk-deep"\n'
                    '[history]\nenabled = false\n')
        # re-run to switch backend; both keys AND the history toggle must survive
        OB.write_choice("deepseek", key_value="")
        data = CFG._load_simple(self.p)
        self.assertEqual(data.get("backend"), "deepseek")
        self.assertEqual(data["env"]["OPENAI_API_KEY"], "sk-openai")
        self.assertEqual(data["env"]["DEEPSEEK_API_KEY"], "sk-deep")
        self.assertEqual(data["history"]["enabled"], False)

    def test_cli_choice_writes_no_key(self):
        OB.write_choice("claude")
        data = CFG._load_simple(self.p)
        self.assertEqual(data.get("backend"), "claude")
        self.assertEqual(data.get("env", {}), {})    # CLIs need no secret

    def test_rerun_preserves_history_dir_and_agents_list(self):
        from pathlib import Path
        with open(self.p, "w") as f:
            f.write('backend = "openai"\n[env]\nOPENAI_API_KEY = "sk-o"\n'
                    '[history]\nenabled = true\ndir = "/tmp/ccstate"\n'
                    '[agents]\nenabled = ["claude"]\n')
        OB.write_choice("claude")                    # change backend only
        txt = Path(self.p).read_text()
        self.assertIn('dir = "/tmp/ccstate"', txt)   # custom state dir survives
        self.assertIn('enabled = ["claude"]', txt)   # agent allow-list survives

    def test_no_secret_temp_file_left_behind_and_mode_is_600(self):
        OB.write_choice("openai", key_value="sk-secret")
        mode = stat.S_IMODE(os.stat(self.p).st_mode)
        self.assertEqual(mode, 0o600)
        leftovers = [f for f in os.listdir(self.dir) if f.startswith(".cc-copilot-")]
        self.assertEqual(leftovers, [])              # temp file cleaned up


class TestApplyToEnv(_Base):
    def test_api_sets_backend_and_key(self):
        OB.apply_to_env("openai", model="gpt-4o", key_value="sk-live")
        self.assertEqual(os.environ.get("CC_COPILOT_BACKEND"), "openai")
        self.assertEqual(os.environ.get("OPENAI_API_KEY"), "sk-live")
        self.assertEqual(os.environ.get("CC_COPILOT_MODEL"), "gpt-4o")

    def test_skip_is_a_noop(self):
        OB.apply_to_env("skip")
        self.assertIsNone(os.environ.get("CC_COPILOT_BACKEND"))


class TestChoiceFor(_Base):
    def test_known_and_skip_aliases(self):
        self.assertEqual(OB.choice_for("codex").kind, "cli")
        self.assertEqual(OB.choice_for("skip").kind, "skip")
        self.assertEqual(OB.choice_for("").kind, "skip")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            OB.choice_for("not-a-backend")

    def test_choice_for_or_none(self):
        # curated API/CLI providers resolve to a Choice…
        self.assertEqual(OB.choice_for_or_none("deepseek").kind, "api")
        self.assertEqual(OB.choice_for_or_none("claude").kind, "cli")
        # …while non-curated backends and skip/empty return None (no key logic).
        self.assertIsNone(OB.choice_for_or_none("ollama"))
        self.assertEqual(OB.choice_for_or_none("ollama-cloud").kind, "api")
        self.assertIsNone(OB.choice_for_or_none("gemini"))
        self.assertIsNone(OB.choice_for_or_none("skip"))
        self.assertIsNone(OB.choice_for_or_none(""))


class TestPersistDefault(_Base):
    """`persist_default` updates the saved default for ANY backend while keeping
    the rest of the config intact; `saved_default` reads it back."""

    def test_saved_default_empty_when_no_file(self):
        self.assertEqual(OB.saved_default(), ("", ""))

    def test_persists_backend_and_model_and_reads_back(self):
        OB.write_choice("deepseek", model="deepseek-chat", key_value="sk-secret")
        OB.persist_default("claude", "")             # switch the default to a CLI backend
        self.assertEqual(OB.saved_default(), ("claude", ""))
        txt = open(self.p, encoding="utf-8").read()
        self.assertIn('backend = "claude"', txt)
        self.assertIn('DEEPSEEK_API_KEY = "sk-secret"', txt)   # secret preserved

    def test_persists_non_curated_backend_that_write_choice_rejects(self):
        # write_choice raises for ollama (no onboarding Choice); persist_default
        # must accept it — /model can land on non-curated backends.
        with self.assertRaises(ValueError):
            OB.write_choice("ollama")
        OB.write_choice("claude")                    # seed a valid config first
        OB.persist_default("ollama", "qwen3")
        self.assertEqual(OB.saved_default(), ("ollama", "qwen3"))

    def test_preserves_history_toggle_dir_and_agents(self):
        with open(self.p, "w") as f:
            f.write('backend = "deepseek"\nmodel = "deepseek-chat"\n'
                    '[env]\nDEEPSEEK_API_KEY = "sk-x"\n'
                    '[history]\nenabled = false\ndir = "/tmp/ccstate"\n'
                    '[agents]\nenabled = ["codex"]\n')
        OB.persist_default("codex", "")
        txt = open(self.p, encoding="utf-8").read()
        self.assertIn("enabled = false", txt)        # history toggle preserved
        self.assertIn('dir = "/tmp/ccstate"', txt)   # custom state dir preserved
        self.assertIn('"codex"', txt)                # agents list preserved
        self.assertIn('DEEPSEEK_API_KEY = "sk-x"', txt)

    def test_written_file_is_chmod_600(self):
        OB.write_choice("claude")
        OB.persist_default("codex", "")
        self.assertEqual(stat.S_IMODE(os.stat(self.p).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
