import json
import unittest

from cccopilot import state as S, transcript as T
from tests.util import asst, user, write


class TestStateCache(unittest.TestCase):
    def tearDown(self):
        S.clear_cache()

    def test_cached_build_reuses_unchanged_transcript_state(self):
        path = write([user("task", 30), asst("done", 5)])
        calls = []

        def parse(p):
            calls.append(p)
            return T.parse(p)

        one = S.cached_build(path, parse)
        two = S.cached_build(path, parse)

        self.assertIs(one, two)
        self.assertEqual(len(calls), 1)

    def test_cached_build_invalidates_when_file_changes(self):
        path = write([user("task", 30)])
        calls = []

        def parse(p):
            calls.append(p)
            return T.parse(p)

        one = S.cached_build(path, parse)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asst("done", 1)) + "\n")
        two = S.cached_build(path, parse)

        self.assertIsNot(one, two)
        self.assertEqual(len(calls), 2)
        self.assertEqual(two.status, "idle")


if __name__ == "__main__":
    unittest.main()
