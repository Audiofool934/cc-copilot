"""The 'what changed since you last looked' renderer."""

import unittest

from cccopilot import state as S, transcript as T, since as SI
from tests.util import asst, result, tool, user, write


def _tr_st(events):
    p = write(events)
    tr = T.parse(p)
    return tr, S.build(tr)


class TestDuration(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(SI.parse_duration("30m"), 1800)
        self.assertEqual(SI.parse_duration("2h"), 7200)
        self.assertEqual(SI.parse_duration("1d"), 86400)
        self.assertEqual(SI.parse_duration("90s"), 90)
        self.assertEqual(SI.parse_duration(" 45M "), 2700)
        self.assertIsNone(SI.parse_duration("soon"))
        self.assertIsNone(SI.parse_duration(""))


class TestSinceByLine(unittest.TestCase):
    def test_nothing_new_when_cutoff_at_tail(self):
        tr, st = _tr_st([user("go", 120), asst("done", 5)])
        v = SI.build(tr, st, since_line=tr.records[-1].line)
        self.assertFalse(v.has_changes)
        self.assertTrue(v.nothing_new)
        self.assertIn("Nothing new", v.text)

    def test_transition_only_delta_is_not_nothing_new(self):
        # a new read-only Read isn't a counted event (only Bash, humans, agent
        # text, failures, and file changes are), but it flips idle → running — a
        # real change the recap should narrate, so nothing_new must be False.
        tr, st = _tr_st([user("go", 300), asst("done", 240),
                         tool("Read", {"file_path": "a.py"}, "r1", 1)])
        v = SI.build(tr, st, since_line=2)
        self.assertEqual(v.new_events, 0)          # nothing counted
        self.assertFalse(v.nothing_new)            # …but the status transition counts
        self.assertNotIn("Nothing new", v.text)

    def test_new_command_after_cutoff_only(self):
        tr, st = _tr_st([
            user("go", 300),
            tool("Bash", {"command": "old-command"}, "t1", 200),
            result("t1", "ok", ago=199),
            tool("Bash", {"command": "new-command"}, "t2", 20),
            result("t2", "ok", ago=19),
        ])
        # cutoff just after the first command's result (line 3)
        v = SI.build(tr, st, since_line=3)
        self.assertTrue(v.has_changes)
        self.assertIn("new-command", v.text)
        self.assertNotIn("old-command", v.text)

    def test_new_failures_surface(self):
        tr, st = _tr_st([
            user("go", 300),
            tool("Bash", {"command": "a"}, "t1", 200),
            result("t1", "ok", ago=199),
            tool("Bash", {"command": "boom"}, "t2", 20),
            result("t2", "segfault", is_error=True, ago=19),
        ])
        v = SI.build(tr, st, since_line=3)
        self.assertIn("New failures", v.text)
        self.assertIn("[L5", v.text)        # the failing result line is cited

    def test_new_human_asks(self):
        tr, st = _tr_st([
            user("first ask", 300), asst("ok", 250),
            user("second ask", 20),
        ])
        v = SI.build(tr, st, since_line=2)
        self.assertIn("second ask", v.text)
        self.assertNotIn("first ask", v.text)

    def test_header_is_time_anchored_not_line_span(self):
        tr, st = _tr_st([
            user("go", 300), asst("working", 240),
            tool("Bash", {"command": "pytest"}, "t1", 180), asst("done", 60),
        ])
        header = SI.build(tr, st, since_line=2, label="last look").text.splitlines()[1]
        self.assertNotIn("watching up to", header)        # the old line-span jargon is gone
        self.assertNotIn("→ now", header)
        self.assertRegex(header, r"since \d{2}:\d{2}")    # clock-time anchor
        self.assertIn("new line", header)                 # how much changed

    def test_header_since_start_for_whole_session(self):
        tr, st = _tr_st([user("go", 300), asst("done", 60)])
        header = SI.build(tr, st, since_line=0, label="all").text.splitlines()[1]
        self.assertIn("since start", header)              # no record before line 0

    def test_pathological_huge_duration_does_not_overflow(self):
        # `/since 999999999d` overflows datetime arithmetic — must degrade to
        # "since start" (cutoff 0, whole session), never crash.
        tr, st = _tr_st([user("go", 300), asst("done", 60)])
        secs = SI.parse_duration("999999999d")
        self.assertEqual(SI.cutoff_line_for_seconds(tr, secs), 0)
        v = SI.build(tr, st, seconds=secs, label="999999999d")    # no OverflowError
        self.assertTrue(v.has_changes)

    def test_command_completed_after_cutoff_is_new(self):
        # call before the cutoff, result after — it finished while you were away
        tr, st = _tr_st([
            user("go", 300),
            tool("Bash", {"command": "long-build"}, "t1", 200),  # line 2 (<= cutoff)
            result("t1", "ok", ago=20),                          # line 3 (> cutoff)
        ])
        v = SI.build(tr, st, since_line=2)
        self.assertTrue(v.has_changes)            # not a false "Nothing new"
        self.assertIn("long-build", v.text)

    def test_edit_completed_after_cutoff_is_new(self):
        tr, st = _tr_st([
            user("edit", 300),
            tool("Edit", {"file_path": "a.py"}, "t1", 200),      # line 2 (<= cutoff)
            result("t1", "ok", ago=20),                          # line 3 (> cutoff)
        ])
        v = SI.build(tr, st, since_line=2)
        self.assertTrue(v.has_changes)
        self.assertIn("a.py", v.text)

    def test_changed_files_surface(self):
        tr, st = _tr_st([
            user("edit it", 200),
            tool("Edit", {"file_path": "a.py"}, "t1", 20),
            result("t1", "ok", ago=19),
        ])
        v = SI.build(tr, st, since_line=1)
        self.assertIn("Files changed", v.text)
        self.assertIn("a.py", v.text)


class TestStateUpto(unittest.TestCase):
    def test_last_seen_ts_is_last_real_ts_not_full_first(self):
        from datetime import datetime, timedelta, timezone
        from cccopilot.transcript import Record, Transcript
        now = datetime.now(timezone.utc).astimezone()
        old = now - timedelta(seconds=5000)
        recent = now - timedelta(seconds=10)
        recs = [
            Record(1, "human", old, text="go"),
            Record(2, "agent_text", recent, text="working"),
            Record(3, "snapshot", None),          # metadata tail, no timestamp
        ]
        tr = Transcript(path="x", records=recs, first_seen_ts=old, last_seen_ts=now)
        old_state = SI._state_upto(tr, 3)
        # must use the last *real* ts in the slice, not the full transcript's first
        self.assertEqual(old_state.tr.last_seen_ts, recent)


class TestSinceByTime(unittest.TestCase):
    def test_time_window_excludes_old(self):
        tr, st = _tr_st([
            user("old ask", 7200), asst("old reply", 7100),   # ~2h ago
            user("recent ask", 600), asst("recent reply", 300),  # <1h ago
        ])
        v = SI.build(tr, st, seconds=3600, label="1h")
        self.assertIn("recent ask", v.text)
        self.assertNotIn("old ask", v.text)


if __name__ == "__main__":
    unittest.main()
