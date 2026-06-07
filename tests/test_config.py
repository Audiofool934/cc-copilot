import os
import tempfile
import unittest

from cccopilot import config as CFG


class _Args:
    backend = None
    model = None


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in
                       ("CC_COPILOT_CONFIG", "CC_COPILOT_BACKEND", "DEEPSEEK_API_KEY")}
        fd, self.p = tempfile.mkstemp(suffix=".toml")
        os.close(fd)
        os.environ["CC_COPILOT_CONFIG"] = self.p

    def tearDown(self):
        os.unlink(self.p)
        for k in ("CC_COPILOT_BACKEND", "DEEPSEEK_API_KEY"):
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def _write(self, body):
        with open(self.p, "w") as f:
            f.write(body)

    def test_env_export_and_defaults(self):
        self._write('backend = "codex"\nmodel = "m1"\n[env]\nDEEPSEEK_API_KEY = "sk-x"\n')
        a = _Args()
        CFG.apply_defaults(a)
        self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), "sk-x")     # [env] exported
        self.assertEqual(os.environ.get("CC_COPILOT_BACKEND"), "codex")  # backend default surfaced
        self.assertEqual(a.model, "m1")                                  # model default applied

    def test_real_env_wins_over_file(self):
        os.environ["CC_COPILOT_BACKEND"] = "claude"
        self._write('backend = "codex"\n')
        a = _Args()
        CFG.apply_defaults(a)
        self.assertEqual(os.environ.get("CC_COPILOT_BACKEND"), "claude")

    def test_explicit_flag_wins(self):
        self._write('model = "m1"\n')
        a = _Args()
        a.model = "flagmodel"
        CFG.apply_defaults(a)
        self.assertEqual(a.model, "flagmodel")

    def test_missing_file_is_noop(self):
        os.unlink(self.p)            # no config file
        a = _Args()
        CFG.apply_defaults(a)        # must not raise
        open(self.p, "w").close()    # recreate so tearDown's unlink succeeds
        self.assertIsNone(a.model)

    def test_template_default_matches_runtime_default(self):
        self.assertIn('backend = "codex"', CFG.TEMPLATE)


if __name__ == "__main__":
    unittest.main()
