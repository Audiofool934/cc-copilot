"""Persisted cockpit UI preferences."""

import os
import tempfile
import unittest

from cccopilot import prefs as PREFS, store as ST


class TestPrefs(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("CC_COPILOT_STATE_DIR", "CC_COPILOT_TIMELINE_HEIGHT")}
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="ccprefs-")
        os.environ.pop("CC_COPILOT_TIMELINE_HEIGHT", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_when_unset(self):
        self.assertEqual(PREFS.get_int("timeline_height", 6), 6)

    def test_set_get_roundtrip(self):
        PREFS.set("timeline_height", 12)
        self.assertEqual(PREFS.get_int("timeline_height", 6), 12)

    def test_env_override_wins(self):
        PREFS.set("timeline_height", 12)
        os.environ["CC_COPILOT_TIMELINE_HEIGHT"] = "20"
        self.assertEqual(PREFS.get_int("timeline_height", 6), 20)

    def test_bad_env_ignored(self):
        PREFS.set("timeline_height", 9)
        os.environ["CC_COPILOT_TIMELINE_HEIGHT"] = "huge"
        self.assertEqual(PREFS.get_int("timeline_height", 6), 9)

    def test_corrupt_file_tolerated(self):
        os.makedirs(ST.state_home(), exist_ok=True)
        with open(os.path.join(ST.state_home(), "ui.json"), "w") as f:
            f.write("not json {")
        self.assertEqual(PREFS.get_int("timeline_height", 6), 6)
        PREFS.set("timeline_height", 7)               # overwrites the corruption
        self.assertEqual(PREFS.get_int("timeline_height", 6), 7)

    def test_bad_stored_value_falls_back(self):
        PREFS.set("timeline_height", "abc")
        self.assertEqual(PREFS.get_int("timeline_height", 6), 6)


if __name__ == "__main__":
    unittest.main()
