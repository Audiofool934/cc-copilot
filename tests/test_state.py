import unittest

from cccopilot import state as S
from tests.util import state, user, user_meta, asst, tool, result


class TestStatus(unittest.TestCase):
    def test_idle_on_agent_text_tail(self):
        self.assertEqual(state([user("do it", 120), asst("done.", 1)]).status, "idle")

    def test_awaiting_on_human_tail(self):
        self.assertEqual(state([asst("ok", 120), user("now X", 1)]).status, "awaiting-agent")

    def test_running_on_recent_tool_result_tail(self):
        st = state([user("x", 120), tool("Bash", {"command": "ls"}, "t1", 10), result("t1", ago=5)])
        self.assertEqual(st.status, "running")

    def test_stalled_on_old_tool_result_tail(self):
        st = state([user("x", 4000), tool("Bash", {"command": "ls"}, "t1", 3700), result("t1", ago=3600)])
        self.assertEqual(st.status, "stalled")

    def test_empty_when_only_local_command(self):
        st = state([user("<command-name>/clear</command-name>", 10)])
        self.assertEqual(st.status, "empty")


class TestFiles(unittest.TestCase):
    def test_failed_edit_excluded(self):
        st = state([user("x", 60),
                    tool("Edit", {"file_path": "/a.py"}, "t1", 10),
                    result("t1", "<tool_use_error>File does not exist</tool_use_error>", is_error=True, ago=9),
                    asst("hmm", 1)])
        self.assertNotIn("/a.py", st.files)

    def test_successful_edit_counted(self):
        st = state([user("x", 60), tool("Edit", {"file_path": "/a.py"}, "t1", 10),
                    result("t1", ago=9), asst("ok", 1)])
        self.assertIn("/a.py", st.files)
        self.assertEqual(st.files["/a.py"].edits, 1)


class TestIntents(unittest.TestCase):
    def test_meta_excluded(self):
        st = state([user_meta("Continue from where you left off", 90), user("real ask", 30), asst("k", 1)])
        texts = [r.text for r in st.intents]
        self.assertIn("real ask", texts)
        self.assertNotIn("Continue from where you left off", texts)

    def test_compact_summary_excluded(self):
        e = user("This session is being continued from a previous conversation", 90)
        e["isCompactSummary"] = True
        st = state([e, user("real", 30), asst("k", 1)])
        self.assertNotIn("This session is being continued from a previous conversation",
                         [r.text for r in st.intents])

    def test_slash_command_recovered(self):
        st = state([user("<command-name>/codex:review</command-name>"
                         "<command-args>--base main</command-args>", 30), asst("k", 1)])
        self.assertTrue(any(r.text.startswith("/codex:review") for r in st.intents))

    def test_housekeeping_slash_excluded(self):
        st = state([user("<command-name>/clear</command-name>", 30), user("real ask", 20), asst("k", 1)])
        self.assertNotIn("/clear", [r.text for r in st.intents])
        self.assertIn("real ask", [r.text for r in st.intents])


class TestAgentWords(unittest.TestCase):
    def test_synthetic_excluded(self):
        st = state([user("x", 60), asst("No response requested.", 30, model="<synthetic>"),
                    asst("real answer", 1)])
        words = [r.text for r in st.last_agent_texts]
        self.assertIn("real answer", words)
        self.assertNotIn("No response requested.", words)


class TestDiff(unittest.TestCase):
    def test_diff_detects_new_failure(self):
        old = state([user("x", 120), asst("a", 60)])
        new = state([user("x", 120), asst("a", 60),
                     tool("Bash", {"command": "c"}, "t1", 30), result("t1", "err", is_error=True, ago=29)])
        d = S.diff(old, new)
        self.assertGreaterEqual(len(d.new_failures), 1)


if __name__ == "__main__":
    unittest.main()
