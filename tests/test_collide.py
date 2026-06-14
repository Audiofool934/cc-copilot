"""Cross-session collision radar: same file mutated by 2+ sessions."""

import time
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

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


    def test_stale_edit_in_resumed_session_filtered_by_since(self):
        # session resumed TODAY (recent tail) but the colliding file was edited
        # weeks ago — with the window cutoff it must NOT count as a live collision.
        old = 30 * 24 * 3600
        resumed = state([user("old work", old),
                         tool("Edit", {"file_path": "x.py"}, "e1", old - 10),
                         result("e1", "ok", ago=old - 15),
                         tool("Bash", {"command": "ls"}, "t2", 5)])   # resumed today
        resumed.tr.git_branch = "feature"
        fresh = _edit_session("x.py", "main")                         # edited just now
        items = [("a", "claude", resumed), ("b", "codex", fresh)]
        # no cutoff → the stale edit collides (the bug)
        self.assertEqual(len(CD.find_collisions(items, "/test/proj")), 1)
        # with a 72h cutoff → the stale edit is dropped, no false collision
        self.assertEqual(
            CD.find_collisions(items, "/test/proj", since=time.time() - 72 * 3600), [])

    def test_party_ts_is_the_edit_time_not_the_session_tail(self):
        # edit early, then unrelated work — party.last_ts must be the edit's time.
        st = state([user("edit it", 300),
                    tool("Edit", {"file_path": "a.py"}, "e1", 250),
                    result("e1", "ok", ago=245),
                    tool("Bash", {"command": "sleep"}, "t2", 5)])   # later, unrelated
        other = _edit_session("a.py", "feature")
        cols = CD.find_collisions([("s1", "claude", st), ("s2", "codex", other)],
                                  "/test/proj")
        party = next(p for c in cols for p in c.parties if p.session_id == "s1")
        edit_ts = next(r.ts for r in st.tr.records if r.line == party.last_line)
        self.assertEqual(party.last_ts, edit_ts)
        self.assertNotEqual(party.last_ts, st.tr.last_ts)   # not the tail

    def test_collisions_folds_claude_subagent_children(self):
        parent = _edit_session("shared.py", "feature")
        codex = _edit_session("shared.py", "main")
        child = _edit_session("shared.py", "feature")
        refs = [types.SimpleNamespace(session_id="p1", agent="claude",
                                      path="/x/p1.jsonl", mtime=time.time()),
                types.SimpleNamespace(session_id="c1", agent="codex",
                                      path="/x/c1.jsonl", mtime=time.time())]
        states = {"/x/p1.jsonl": parent, "/x/c1.jsonl": codex,
                  "/x/p1/subagents/agent-k.jsonl": child}
        with mock.patch.object(CD.SRC, "list_sessions", return_value=refs), \
             mock.patch.object(CD.LOC, "subagent_paths",
                               side_effect=lambda p: ["/x/p1/subagents/agent-k.jsonl"]
                               if p == "/x/p1.jsonl" else []), \
             mock.patch.object(CD.SRC, "parse", side_effect=lambda p: p), \
             mock.patch.object(CD.S, "build", side_effect=lambda tr: states[tr]):
            cols = CD.collisions("/x", now=time.time())
        c = next(c for c in cols if c.path == "shared.py")
        self.assertIn("agent-k", {p.session_id for p in c.parties})   # child surfaced
        self.assertTrue(c.cross_branch)

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
