"""`/since` as a grounded LLM recap (recap-by-default, deterministic fallback)."""

import json
import os
import tempfile
import unittest

from cccopilot import chat as C, narrate as N


def _write_session(n_events=4):
    d = tempfile.mkdtemp(prefix="ccsincerecap-")
    p = os.path.join(d, "s.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "sessionId": "s", "cwd": "/x",
                            "message": {"role": "user", "content": "go"}}) + "\n")
        for i in range(n_events):
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                    "model": "c", "content": [{"type": "tool_use", "id": f"t{i}",
                    "name": "Bash", "input": {"command": f"cmd-{i}", "description": ""}}]}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                 "content": "ok"}]}}) + "\n")
    return p


class TestSinceRecap(unittest.TestCase):
    def setUp(self):
        self._real_recap = N.recap_since
        self._real_avail = N.available
        self._saved_state = os.environ.get("CC_COPILOT_STATE_DIR")
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="ccsincestate-")
        self.calls = []

        def fake_recap(text, model=None, backend=None):
            self.calls.append(text)
            return "RECAP: the agent ran some commands [L4]; safe to continue."
        N.recap_since = fake_recap
        N.available = lambda be=None: True
        p = _write_session()
        self.sess = C.ChatSession(p, backend="codex", alerts=False, persist=False)

    def tearDown(self):
        N.recap_since = self._real_recap
        N.available = self._real_avail
        if self._saved_state is None:
            os.environ.pop("CC_COPILOT_STATE_DIR", None)
        else:
            os.environ["CC_COPILOT_STATE_DIR"] = self._saved_state

    def test_recap_by_default_with_evidence_beneath(self):
        out = self.sess._since("30m")
        self.assertTrue(out.startswith("# 🛰  recap"))         # narrative heading
        self.assertIn("RECAP: the agent ran", out)             # the model's prose
        self.assertIn("evidence —", out)                       # cited delta kept beneath
        self.assertIn("[L", out)                               # citations preserved
        self.assertEqual(len(self.calls), 1)                   # model was called once

    def test_model_sees_the_cited_delta(self):
        self.sess._since("30m")
        self.assertIn("[L", self.calls[0])                     # evidence had citations
        self.assertIn("Commands", self.calls[0])               # the deterministic delta

    def test_raw_flag_forces_deterministic_no_model(self):
        out = self.sess._since("30m --raw")
        self.assertFalse(out.startswith("# 🛰  recap"))
        self.assertEqual(self.calls, [])                       # no model call

    def test_no_backend_falls_back_to_deterministic(self):
        N.available = lambda be=None: False
        out = self.sess._since("30m")
        self.assertFalse(out.startswith("# 🛰  recap"))
        self.assertEqual(self.calls, [])

    def test_nothing_new_skips_the_model(self):
        self.sess.mark_lastlook()                              # mark at the current tail
        out = self.sess._since("last-look")                    # empty delta
        self.assertEqual(self.calls, [])                       # no point recapping nothing
        self.assertNotIn("# 🛰  recap", out)

    def test_recap_failure_falls_back_to_evidence(self):
        def boom(text, model=None, backend=None):
            raise RuntimeError("backend exploded")
        N.recap_since = boom
        out = self.sess._since("30m")
        self.assertIn("recap unavailable", out)
        self.assertIn("[L", out)                               # evidence still shown

    def test_since_view_parses_raw_and_returns_triple(self):
        res = self.sess._since_view("30m --raw")
        self.assertIsInstance(res, tuple)
        view, raw, commit = res
        self.assertTrue(raw)
        self.assertTrue(view.has_changes)
        self.assertTrue(callable(commit))

    def test_since_view_edge_message_is_str(self):
        # an unknown duration is an edge-case string, not a (view, raw, commit) tuple
        res = self.sess._since_view("banana")
        self.assertIsInstance(res, str)
        self.assertIn("unknown time", res)

    def test_last_look_marker_consumed_only_on_commit(self):
        """The marker must advance only when the recap is actually shown — so a
        dropped async recap (after an evidence switch) doesn't lose the delta."""
        from cccopilot import lastlook as LL
        from cccopilot.chat import _now_iso
        key = self.sess._lastlook_key()
        LL.mark(key, 1, "", _now_iso())           # an early mark → a real delta
        view, raw, commit = self.sess._since_view("last-look")
        self.assertTrue(view.has_changes)
        self.assertEqual(int(LL.get(key)["line"]), 1)      # NOT advanced yet
        commit()
        self.assertGreater(int(LL.get(key)["line"]), 1)    # advanced once shown

    def test_deferred_commit_never_rewinds_a_concurrent_advance(self):
        """While a recap is pending, another /since (or a second cockpit) can move
        the marker forward; the deferred commit must not rewind it to its older
        captured tail and re-surface already-reviewed lines."""
        from cccopilot import lastlook as LL
        from cccopilot.chat import _now_iso
        key = self.sess._lastlook_key()
        LL.mark(key, 1, "", _now_iso())
        view, raw, commit = self.sess._since_view("last-look")   # captures the tail (~9)
        LL.mark(key, 100, "newer", _now_iso())    # a concurrent render advanced past it
        commit()                                   # must NOT move it back to ~9
        self.assertEqual(int(LL.get(key)["line"]), 100)


if __name__ == "__main__":
    unittest.main()
