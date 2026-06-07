import json
import os
import tempfile
import unittest

from cccopilot import cli, observe as O, scope as SC, state as S, transcript as T
from tests.util import asst, result, tool, user, write


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


if __name__ == "__main__":
    unittest.main()
