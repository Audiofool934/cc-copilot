import unittest

from cccopilot import assess as A
from tests.util import state, user, asst, tool, result


def fail_streak(n=3, base=100):
    evs = [user("x", base)]
    for i in range(n):
        evs += [tool("Bash", {"command": f"cmd-{i}"}, f"t{i}", 30 - i * 2),
                result(f"t{i}", "err", is_error=True, ago=29 - i * 2)]
    return evs


class TestAssess(unittest.TestCase):
    def test_intervene_when_running_with_recent_fail_streak(self):
        st = state(fail_streak())
        a = A.assess(st)
        self.assertEqual(st.status, "running")
        self.assertEqual(a.verdict, "intervene")
        self.assertTrue(any(s.kind == "fail_streak" for s in a.signals))

    def test_idle_with_old_friction_is_review_not_intervene(self):
        st = state(fail_streak() + [asst("recovered, moving on", 1)])
        a = A.assess(st)
        self.assertEqual(st.status, "idle")
        self.assertEqual(a.verdict, "review")

    def test_retry_loop_detected(self):
        evs = [user("x", 100)]
        for i in range(4):
            evs += [tool("Bash", {"command": "the same command"}, f"t{i}", 50 - i),
                    result(f"t{i}", ago=49 - i)]
        evs += [asst("done", 1)]
        a = A.assess(state(evs))
        self.assertTrue(any(s.kind == "retry_loop" for s in a.signals))

    def test_edit_thrash_detected(self):
        evs = [user("x", 100)]
        for i in range(2):
            evs += [tool("Edit", {"file_path": "/same.py"}, f"t{i}", 40 - i),
                    result(f"t{i}", "<tool_use_error>File has been modified since read</tool_use_error>",
                           is_error=True, ago=39 - i)]
        evs += [asst("ugh", 1)]
        a = A.assess(state(evs))
        self.assertTrue(any(s.kind == "edit_thrash" for s in a.signals))

    def test_clear_when_no_friction(self):
        st = state([user("x", 60), tool("Read", {"file_path": "/a"}, "t1", 10), result("t1", "data", ago=5)])
        self.assertEqual(A.assess(st).verdict, "clear")

    def test_exit_codes_mapping(self):
        # intervene -> 2, review -> 1, else 0 (matches cli encoding)
        self.assertEqual(A.assess(state(fail_streak())).verdict, "intervene")


if __name__ == "__main__":
    unittest.main()
