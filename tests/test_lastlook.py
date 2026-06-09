"""Last-look markers: persistence, gating, and corruption tolerance."""

import os
import tempfile
import unittest

from cccopilot import lastlook as LL


class TestLastLook(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY")}
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="ccll-")
        os.environ.pop("CC_COPILOT_HISTORY", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_mark_get_roundtrip(self):
        LL.mark("sess1", 42, "2026-06-08T10:00:00Z", "2026-06-08T11:00:00")
        m = LL.get("sess1")
        self.assertEqual(m["line"], 42)
        self.assertEqual(m["ts"], "2026-06-08T10:00:00Z")
        self.assertEqual(m["looked_at"], "2026-06-08T11:00:00")

    def test_independent_keys(self):
        LL.mark("a", 1)
        LL.mark("b", 2)
        self.assertEqual(LL.get("a")["line"], 1)
        self.assertEqual(LL.get("b")["line"], 2)

    def test_get_unknown_is_none(self):
        self.assertIsNone(LL.get("nope"))
        self.assertIsNone(LL.get(""))

    def test_advance_is_forward_only(self):
        LL.mark("s", 50, "t50", "l50")
        LL.advance("s", 30, "t30", "l30")           # older → ignored (no rewind)
        self.assertEqual(LL.get("s")["line"], 50)
        self.assertEqual(LL.get("s")["ts"], "t50")  # untouched
        LL.advance("s", 80, "t80", "l80")           # newer → moves forward
        self.assertEqual(LL.get("s")["line"], 80)
        self.assertEqual(LL.get("s")["ts"], "t80")

    def test_advance_from_unset_sets_it(self):
        LL.advance("fresh", 7)
        self.assertEqual(LL.get("fresh")["line"], 7)

    def test_forget(self):
        LL.mark("x", 5)
        LL.forget("x")
        self.assertIsNone(LL.get("x"))

    def test_key_for(self):
        self.assertEqual(LL.key_for("the-id", "/p/x.jsonl"), "the-id")
        self.assertEqual(LL.key_for("", "/p/rollout-abc.jsonl"), "rollout-abc")
        self.assertEqual(LL.key_for("", ""), "")

    def test_disabled_is_noop(self):
        os.environ["CC_COPILOT_HISTORY"] = "0"
        LL.mark("y", 9)
        self.assertIsNone(LL.get("y"))   # neither written nor readable when off

    def test_corrupt_file_tolerated(self):
        from cccopilot import store as ST
        os.makedirs(ST.state_home(), exist_ok=True)
        with open(os.path.join(ST.state_home(), "lastlook.json"), "w") as f:
            f.write("{ this is not json")
        self.assertIsNone(LL.get("z"))   # tolerated, no crash
        LL.mark("z", 7)                  # overwrites the corruption
        self.assertEqual(LL.get("z")["line"], 7)

    def test_corrupt_marker_value_sanitized(self):
        # a valid-JSON but wrong-typed line must not crash int(...) in callers
        import json
        from cccopilot import store as ST
        os.makedirs(ST.state_home(), exist_ok=True)
        with open(os.path.join(ST.state_home(), "lastlook.json"), "w") as f:
            json.dump({"s": {"line": "bad", "ts": 5}}, f)
        m = LL.get("s")
        self.assertEqual(m["line"], 0)             # coerced, not a crash
        self.assertIsInstance(m["line"], int)
        self.assertIsInstance(m["ts"], str)

    def test_mark_then_get_is_atomic_typed(self):
        LL.mark("q", "12")          # caller passes a str line
        self.assertEqual(LL.get("q")["line"], 12)


if __name__ == "__main__":
    unittest.main()
