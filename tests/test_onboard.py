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
         "XAI_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY",
         "TOGETHER_API_KEY", "FIREWORKS_API_KEY", "CEREBRAS_API_KEY",
         "DEEPINFRA_API_KEY", "HUGGINGFACE_API_KEY", "NVIDIA_API_KEY",
         "CHUTES_API_KEY", "NOVITA_API_KEY", "VENICE_API_KEY",
         "ARCEE_API_KEY", "GMI_API_KEY", "STEPFUN_API_KEY", "XIAOMI_API_KEY",
         "VOLCENGINE_API_KEY", "TENCENT_TOKENHUB_API_KEY")


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
        self.assertEqual(labels[:2], ["Claude", "Codex"])     # CLIs first
        self.assertEqual(labels[-1], "Skip for now")          # skip last


class TestWriteChoice(_Base):
    def test_api_choice_writes_backend_model_and_key(self):
        OB.write_choice("openai", model="gpt-4o", key_value="sk-secret")
        data = CFG._load_simple(self.p)
        self.assertEqual(data.get("backend"), "openai")
        self.assertEqual(data.get("model"), "gpt-4o")
        self.assertEqual(data["env"]["OPENAI_API_KEY"], "sk-secret")

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
        self.assertIsNone(OB.choice_for_or_none("gemini"))
        self.assertIsNone(OB.choice_for_or_none("skip"))
        self.assertIsNone(OB.choice_for_or_none(""))


if __name__ == "__main__":
    unittest.main()
