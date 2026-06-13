"""The handoff Markdown artifact."""

import unittest

from cccopilot import handoff as HO, since as SI, state as S, transcript as T
from tests.util import asst, result, tool, user, write


def _tr_st(events):
    p = write(events)
    tr = T.parse(p)
    return tr, S.build(tr)


class TestHandoff(unittest.TestCase):
    def test_has_metadata_and_full_brief(self):
        tr, st = _tr_st([user("ship it", 120), asst("done", 5)])
        md = HO.render(st, agent="codex", generated_at="2026-06-08 12:00")
        self.assertIn("# Handoff —", md)
        self.assertIn("**Agent:** codex", md)
        self.assertIn("**Generated:** 2026-06-08 12:00", md)
        self.assertIn("## Full brief", md)
        self.assertIn("cc-copilot brief", md)        # the brief body is embedded

    def test_title_is_single_line(self):
        tr, st = _tr_st([user("line one\nline two\nline three", 60)])
        md = HO.render(st, agent="claude")
        title_line = md.splitlines()[0]
        self.assertTrue(title_line.startswith("# Handoff —"))
        self.assertNotIn("\nline two", title_line)   # flattened

    def test_since_section_included_when_changes(self):
        tr, st = _tr_st([
            user("go", 200),
            tool("Bash", {"command": "new-cmd"}, "t1", 20),
            result("t1", "ok", ago=19),
        ])
        sv = SI.build(tr, st, since_line=1, label="last look")
        self.assertTrue(sv.has_changes)
        md = HO.render(st, agent="claude", since_view=sv)
        self.assertIn("## While you were away", md)
        self.assertIn("new-cmd", md)

    def test_since_section_omitted_when_no_changes(self):
        tr, st = _tr_st([user("go", 60), asst("done", 5)])
        sv = SI.build(tr, st, since_line=tr.records[-1].line)
        md = HO.render(st, agent="claude", since_view=sv)
        self.assertNotIn("## While you were away", md)

    def test_default_filename(self):
        tr, st = _tr_st([user("go", 10)])
        name = HO.default_filename(tr, agent="codex", stamp="2026-06-08 12:00")
        self.assertTrue(name.startswith("handoff-"))
        self.assertTrue(name.endswith(".md"))
        self.assertIn("codex", name)


class TestHandoffSinceGate(unittest.TestCase):
    def test_transition_only_delta_still_shows_while_away(self):
        # status/safety flip with zero counted events: new_events==0 but the
        # since view is NOT "nothing new" — the section must still render.
        tr, st = _tr_st([user("ship it", 120), asst("done", 5)])
        sv = SI.SinceView(cutoff_line=1, label="30m", new_events=0,
                          text="# Since\nstatus changed", nothing_new=False)
        self.assertIn("While you were away", HO.render(st, since_view=sv))

    def test_truly_empty_delta_is_still_omitted(self):
        tr, st = _tr_st([user("ship it", 120), asst("done", 5)])
        sv = SI.SinceView(cutoff_line=1, label="30m", new_events=0,
                          text="Nothing new", nothing_new=True)
        self.assertNotIn("While you were away", HO.render(st, since_view=sv))


if __name__ == "__main__":
    unittest.main()
