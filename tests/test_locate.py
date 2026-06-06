import json
import os
import tempfile
import unittest

from cccopilot import locate as L


def _jsonl(obj):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(p, "w") as f:
        f.write(json.dumps(obj) + "\n")
    return p


class TestLocate(unittest.TestCase):
    def test_encode_cwd(self):
        self.assertEqual(L.encode_cwd("/Users/a/Projects"), "-Users-a-Projects")
        self.assertEqual(L.encode_cwd("/x/a.b.c"), "-x-a-b-c")
        self.assertEqual(L.encode_cwd("/p/My Repo"), "-p-My-Repo")

    def test_is_own_session_true(self):
        p = _jsonl({"type": "user", "message": {"role": "user",
                   "content": "You are cc-copilot's narration layer. Below is a brief…"}})
        try:
            self.assertTrue(L.is_own_session(p))
        finally:
            os.unlink(p)

    def test_is_own_session_false(self):
        p = _jsonl({"type": "user", "message": {"role": "user", "content": "fix the bug"}})
        try:
            self.assertFalse(L.is_own_session(p))
        finally:
            os.unlink(p)

    def test_read_cwd(self):
        p = _jsonl({"type": "user", "cwd": "/my/proj",
                   "message": {"role": "user", "content": "hi"}})
        try:
            self.assertEqual(L.read_cwd(p), "/my/proj")
        finally:
            os.unlink(p)

    def test_ago_formatting(self):
        import time
        self.assertTrue(L.ago(time.time() - 30).endswith("m"))
        self.assertTrue(L.ago(time.time() - 7200).endswith("h"))
        self.assertTrue(L.ago(time.time() - 200000).endswith("d"))


if __name__ == "__main__":
    unittest.main()
