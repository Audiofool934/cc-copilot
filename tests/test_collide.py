"""Cross-session collision radar: same file mutated by 2+ sessions."""

import unittest
from datetime import datetime, timezone

from cccopilot import collide as CD
from tests.util import result, state, tool, user


def _edit_session(path, branch):
    st = state([user("work on it", 100),
                tool("Edit", {"file_path": path}, "e1", 50),
                result("e1", "ok", ago=49)])
    st.tr.git_branch = branch
    return st


class TestCollide(unittest.TestCase):
    def test_cross_branch_collision_detected(self):
        st1 = _edit_session("cccopilot/tui.py", "main")
        st2 = _edit_session("cccopilot/tui.py", "feature")
        cols = CD.find_collisions([("s1", "claude", st1), ("s2", "codex", st2)],
                                  "/test/proj")
        self.assertEqual(len(cols), 1)
        self.assertTrue(cols[0].cross_branch)
        self.assertEqual(cols[0].path, "cccopilot/tui.py")
        self.assertEqual({p.agent for p in cols[0].parties}, {"claude", "codex"})

    def test_absolute_and_relative_paths_match(self):
        st1 = _edit_session("/test/proj/cccopilot/tui.py", "main")   # absolute
        st2 = _edit_session("cccopilot/tui.py", "feature")            # relative
        cols = CD.find_collisions([("s1", "claude", st1), ("s2", "codex", st2)],
                                  "/test/proj")
        self.assertEqual(len(cols), 1)
        self.assertEqual(cols[0].path, "cccopilot/tui.py")

    def test_single_session_is_no_collision(self):
        st1 = _edit_session("a.py", "main")
        self.assertEqual(CD.find_collisions([("s1", "claude", st1)], "/test/proj"), [])

    def test_same_branch_collision_is_not_cross_branch(self):
        st1 = _edit_session("a.py", "main")
        st2 = _edit_session("a.py", "main")
        cols = CD.find_collisions([("s1", "x", st1), ("s2", "y", st2)], "/test/proj")
        self.assertEqual(len(cols), 1)
        self.assertFalse(cols[0].cross_branch)

    def test_cross_branch_sorted_first(self):
        same1 = _edit_session("same.py", "main")
        same2 = _edit_session("same.py", "main")
        cross1 = _edit_session("cross.py", "main")
        cross2 = _edit_session("cross.py", "feature")
        cols = CD.find_collisions([("a", "x", same1), ("b", "y", same2),
                                   ("c", "x", cross1), ("d", "y", cross2)],
                                  "/test/proj")
        self.assertEqual(cols[0].path, "cross.py")     # cross-branch ranked first
        self.assertTrue(cols[0].cross_branch)


    def test_party_ordering_uses_full_timestamp_across_midnight(self):
        # yesterday 23:50 must sort BEHIND today 00:10 — HH:MM alone gets it wrong.
        late_yday = CD.Party("a", "claude", "main", "idle", 1, "23:50",
                             datetime(2026, 6, 14, 23, 50, tzinfo=timezone.utc))
        early_today = CD.Party("b", "codex", "feat", "idle", 1, "00:10",
                               datetime(2026, 6, 15, 0, 10, tzinfo=timezone.utc))
        ordered = sorted([late_yday, early_today], key=CD._party_recency, reverse=True)
        self.assertIs(ordered[0], early_today)
        # the old HH:MM-string sort would (incorrectly) put yesterday first:
        hhmm = sorted([late_yday, early_today], key=lambda p: p.last_hhmm, reverse=True)
        self.assertIs(hhmm[0], late_yday)


if __name__ == "__main__":
    unittest.main()
