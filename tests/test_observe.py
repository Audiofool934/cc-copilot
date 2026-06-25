import json
import os
import tempfile
import unittest
from unittest import mock

from cccopilot import cli, observe as O, scope as SC, state as S, transcript as T
from tests.util import asst, result, state, tool, user, write


def _write_at(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


class TestObserveReport(unittest.TestCase):
    def test_single_session_report_surfaces_next_decision(self):
        p = write([
            user("fix the failing test", 4000),
            tool("Bash", {"command": "pytest"}, "t1", 3700),
            result("t1", "failed", is_error=True, ago=3600),
        ])
        st = S.build(T.parse(p))

        out = O.render(p, st)

        self.assertIn("cc-copilot observe", out)
        self.assertIn("## Attention Queue", out)
        self.assertIn("## Next Human Decision", out)
        self.assertIn("INTERVENE", out)
        self.assertIn("[L3]", out)

    def test_multi_session_report_ranks_attention_first(self):
        cwd = tempfile.mkdtemp(prefix="ccobserve-proj-")
        d = tempfile.mkdtemp(prefix="ccobserve-sessions-")
        stalled = _write_at(os.path.join(d, "sess-A.jsonl"), [
            user("fix tests", 4000, sessionId="sess-A", cwd=cwd),
            tool("Bash", {"command": "pytest"}, "t1", 3700),
            result("t1", "failed", is_error=True, ago=3600),
        ])
        _write_at(os.path.join(d, "sess-B.jsonl"), [
            user("update docs", 120, sessionId="sess-B", cwd=cwd),
            asst("done", 5),
        ])
        st = S.build(T.parse(stalled))

        out = O.render(stalled, st, "multi-session")

        self.assertIn("scope `multi-session`", out)
        self.assertLess(out.index("`sess-A`"), out.index("`sess-B`"))
        self.assertIn("[sess-A:L3]", out)

    def test_timeline_lines_return_clear_when_nothing_needs_attention(self):
        p = write([user("document it", 60), asst("done", 5)])
        st = S.build(T.parse(p))

        lines = O.timeline_lines(p, st, SC.SESSION)

        self.assertEqual(lines[0][0], "clear")
        self.assertIn("closing message", lines[0][1])

    def test_cli_parser_accepts_observe_scope(self):
        args = cli.build_parser().parse_args(["observe", "--scope", "repo"])
        self.assertEqual(args.cmd, "observe")
        self.assertEqual(args.scope, SC.PROJECT)

    def test_project_glance_disables_repo_configured_git_hooks(self):
        import types

        seen = []

        def fake_run(cmd, **kw):
            seen.append((cmd, kw))
            if "rev-parse" in cmd:
                return types.SimpleNamespace(returncode=0, stdout="/repo\n")
            if "branch" in cmd:
                return types.SimpleNamespace(returncode=0, stdout="main\n")
            return types.SimpleNamespace(returncode=0, stdout="")

        with mock.patch("cccopilot.observe.subprocess.run", side_effect=fake_run):
            out = O._project_glance("/repo")

        self.assertIn("Project Glance", out[0])
        status_cmd = seen[2][0]
        self.assertIn("core.fsmonitor=false", status_cmd)
        self.assertIn("core.hooksPath=/dev/null", status_cmd)
        self.assertEqual(seen[2][1]["env"]["GIT_OPTIONAL_LOCKS"], "0")


class TestNextStep(unittest.TestCase):
    """`observe.next_step` is the deterministic, LLM-free fallback behind `/now`."""

    def test_idle_session_recommends_reading_the_closing_message(self):
        p = write([user("document it", 60), asst("all done", 5)])
        st = S.build(T.parse(p))
        out = O.next_step(p, st)
        self.assertTrue(out.startswith("→ "))
        self.assertIn("closing message", out)           # READY decision

    def test_stalled_session_recommends_intervening(self):
        p = write([user("fix the build", 4000),
                   tool("Bash", {"command": "make"}, "t1", 3700),
                   result("t1", "boom", is_error=True, ago=3600)])   # mid-turn, >180s old
        st = S.build(T.parse(p))
        out = O.next_step(p, st)
        self.assertIn("Intervene", out)

    def test_no_live_evidence_is_a_clean_suggestion_not_a_crash(self):
        out = O.next_step("/does/not/exist.jsonl", st=None)
        self.assertIn("no live session evidence", out)

    def test_multi_scope_leads_with_the_neediest_and_appends_siblings(self):
        cwd = tempfile.mkdtemp(prefix="ccnext-proj-")
        d = tempfile.mkdtemp(prefix="ccnext-sessions-")
        stalled = _write_at(os.path.join(d, "sess-A.jsonl"), [
            user("fix tests", 4000, sessionId="sess-A", cwd=cwd),
            tool("Bash", {"command": "pytest"}, "t1", 3700),
            result("t1", "failed", is_error=True, ago=3600),
        ])
        _write_at(os.path.join(d, "sess-B.jsonl"), [
            user("ship it", 3000, sessionId="sess-B", cwd=cwd),
            tool("Bash", {"command": "deploy"}, "t2", 2800),     # also mid-turn/stalled
            result("t2", "no", is_error=True, ago=2700),
        ])
        st = S.build(T.parse(stalled))
        out = O.next_step(stalled, st, "multi-session")
        self.assertTrue(out.startswith("→ "))            # primary decision
        self.assertIn("sess-A", out)                     # neediest leads
        self.assertIn("also:", out)                      # the second needy sibling surfaces


class TestFleetBoard(unittest.TestCase):
    """`chat.render_fleet` backs `cc-copilot status`, REPL `/status`, cockpit `/status`."""

    def test_ranks_neediest_first_and_counts(self):
        from cccopilot import chat as C
        stalled = write([user("fix", 4000),
                         tool("Bash", {"command": "x"}, "t1", 3700),
                         result("t1", "boom", is_error=True, ago=3600)])
        idle = write([user("doc", 120), asst("all done", 5)])

        class _R:
            def __init__(s, p, sid):
                s.path, s.session_id, s.own, s.agent = p, sid, False, "claude"

        refs = [_R(idle, "idle0000"), _R(stalled, "stall000")]
        with mock.patch.object(C.SRC, "list_sessions", return_value=refs):
            text, n = C.render_fleet("/proj")
        self.assertEqual(n, 2)
        self.assertIn("cc-copilot status", text)
        self.assertLess(text.index("stall000"), text.index("idle0000"))  # neediest first

    def test_no_sessions_is_a_clean_message_and_zero_count(self):
        from cccopilot import chat as C
        with mock.patch.object(C.SRC, "list_sessions", return_value=[]):
            text, n = C.render_fleet("/empty/proj")
        self.assertEqual(n, 0)
        self.assertIn("no work sessions", text)


class TestRecentEvidenceOrdering(unittest.TestCase):
    def test_recent_evidence_orders_across_sessions_by_time_not_line(self):
        # OLD: a stale failure buried at a HIGH line number.
        old = [user("old session", 4000)]
        for i in range(8):
            old += [asst(f"step {i}", 3900 - i * 10)]
        old += [tool("Bash", {"command": "make"}, "t1", 3700),
                result("t1", "boom", is_error=True, ago=3600)]
        # NEW: a fresh failure at a LOW line number.
        new = [user("new session", 100),
               tool("Bash", {"command": "pytest"}, "t2", 60),
               result("t2", "fail", is_error=True, ago=30)]

        item_old = O.ObservationItem(ref=None, st=state(old), assessment=None,
                                     session_id="OLD", title="")
        item_new = O.ObservationItem(ref=None, st=state(new), assessment=None,
                                     session_id="NEW", title="")
        fails = [r for r in O._recent_evidence([item_old, item_new], True)
                 if "failed" in r]
        order = ["NEW" if "`NEW`" in r else "OLD" for r in fails]
        self.assertEqual(order.index("NEW"), 0)   # fresh failure first, not the long stale one


class TestTimelineRobustness(unittest.TestCase):
    def test_unreadable_transcript_is_a_clean_warn(self):
        # build() parses the anchor transcript unconditionally; a non-ValueError
        # (e.g. PermissionError on a vanished/locked file) used to escape.
        with mock.patch("cccopilot.observe.os.path.isfile", return_value=True), \
             mock.patch.object(O.SRC, "parse", side_effect=PermissionError("nope")):
            lines = O.timeline_lines("/tmp/whatever.jsonl", st=None, scope="session")
        self.assertEqual(lines, [("warn", "attention: transcript unavailable")])


class TestObserveInfoDrift(unittest.TestCase):
    def test_drift_shows_in_now_but_not_attention_queue(self):
        evs = [user("implement the redaction module for secrets", 600)]
        for i in range(12):
            evs += [tool("Edit", {"file_path": f"ui/button{i}.tsx"}, f"e{i}", 300 - i * 5),
                    result(f"e{i}", "ok", ago=299 - i * 5)]
        evs.append(asst("tweaked the button colors", 2))
        p = write(evs)
        st = S.build(T.parse(p))

        out = O.render(p, st)

        # surfaced as an info heads-up in Now …
        self.assertIn("no longer references the original goal", out)
        # … but it must not drag the session into the attention queue
        queue = out.split("## Attention Queue", 1)[1].split("##", 1)[0]
        self.assertIn("nothing currently needs human attention", queue)


if __name__ == "__main__":
    unittest.main()
