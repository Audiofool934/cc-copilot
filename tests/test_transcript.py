import io
import json
import os
import tempfile
import unittest

from cccopilot import transcript as T
from cccopilot.transcript import _parse_slash_command
from tests.util import write, user, user_meta, asst, tool, result


class TestTranscript(unittest.TestCase):
    def test_meta_not_emitted_as_human(self):
        tr = T.parse(write([user_meta("Continue", 60), asst("real", 1)]))
        self.assertEqual([r for r in tr.records if r.kind == "human"], [])

    def test_synthetic_not_emitted_as_agent_text(self):
        tr = T.parse(write([asst("No response requested.", 30, model="<synthetic>"), asst("real", 1)]))
        texts = [r.text for r in tr.records if r.kind == "agent_text"]
        self.assertIn("real", texts)
        self.assertNotIn("No response requested.", texts)

    def test_slash_command_parse(self):
        self.assertEqual(_parse_slash_command("<command-name>/foo</command-name>"), "/foo")
        self.assertEqual(
            _parse_slash_command("<command-name>/foo</command-name><command-args>a b</command-args>"),
            "/foo a b")
        self.assertIsNone(_parse_slash_command("just a normal message"))

    def test_tool_call_result_pairing(self):
        tr = T.parse(write([tool("Bash", {"command": "ls"}, "t1", 10), result("t1", "out", ago=9)]))
        kinds = [r.kind for r in tr.records]
        self.assertIn("tool_call", kinds)
        self.assertIn("tool_result", kinds)

    def test_session_metadata_captured(self):
        tr = T.parse(write([user("hi", 1)]))
        self.assertEqual(tr.cwd, "/test/proj")
        self.assertEqual(tr.git_branch, "main")
        self.assertEqual(tr.session_id, "testsess")

    def test_custom_title_captured(self):
        tr = T.parse(write([
            {"type": "custom-title", "customTitle": "test-session-A", "sessionId": "testsess"},
            user("hi", 1),
        ]))
        self.assertEqual(tr.title, "test-session-A")


class TestTranscriptRobustness(unittest.TestCase):
    def test_slash_only_command_name_does_not_crash(self):
        # a command name made only of slashes used to IndexError in _ingest
        for name in ("/", "//", "///"):
            tr = T.parse(write([user(f"<command-name>{name}</command-name>", 60),
                                asst("ok", 1)]))
            texts = [r.text for r in tr.records if r.kind == "agent_text"]
            self.assertIn("ok", texts)   # parse got past the slash-only line


class TestCappedLines(unittest.TestCase):
    def test_read_capped_lines_bounds_and_recovers(self):
        data = "short1\n" + ("x" * 50) + "\n" + "short2\n"
        out = list(T.read_capped_lines(io.StringIO(data), cap=10))
        self.assertEqual(len(out), 3)                 # one yield per physical line
        self.assertEqual([c for _t, c in out], [False, True, False])
        self.assertLessEqual(len(out[1][0]), 10)      # giant line's tail discarded
        self.assertEqual(out[2][0].strip(), "short2")  # recovers cleanly after a clip

    def test_final_line_without_newline_is_not_clipped(self):
        out = list(T.read_capped_lines(io.StringIO("abc"), cap=10))
        self.assertEqual(out, [("abc", False)])

    def test_parse_survives_pathological_giant_line(self):
        # A multi-MB single line must not be buffered/parsed — it's counted as a
        # parse error, and the valid records around it survive with correct lines.
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        try:
            with open(p, "w") as fh:
                fh.write(json.dumps(user("hello", 60)) + "\n")
                fh.write("z" * (T.MAX_LINE_CHARS + 50_000) + "\n")   # >1MB junk
                fh.write(json.dumps(asst("done", 1)) + "\n")
            tr = T.parse(p)
            self.assertEqual(tr.parse_errors, 1)
            human = [r for r in tr.records if r.kind == "human"]
            agent = [r for r in tr.records if r.kind == "agent_text"]
            self.assertEqual([r.line for r in human], [1])    # citations stay aligned
            self.assertEqual([r.line for r in agent], [3])
        finally:
            os.unlink(p)


if __name__ == "__main__":
    unittest.main()
