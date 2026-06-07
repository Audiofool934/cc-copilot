import json
import os
import shutil
import tempfile
import types
import unittest

from cccopilot import locate as L


def _jsonl(obj):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(p, "w") as f:
        f.write(json.dumps(obj) + "\n")
    return p


def _jsonl_lines(path, objs):
    with open(path, "w", encoding="utf-8") as f:
        for obj in objs:
            f.write(json.dumps(obj) + "\n")


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

    def test_read_title_latest_wins_across_title_formats(self):
        p = _jsonl({"type": "ai-title", "aiTitle": "old title"})
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "custom-title",
                                    "customTitle": "test-session-A"}) + "\n")
            self.assertEqual(L.read_title(p), "test-session-A")
        finally:
            os.unlink(p)

    def test_ago_formatting(self):
        import time
        self.assertTrue(L.ago(time.time() - 30).endswith("m"))
        self.assertTrue(L.ago(time.time() - 7200).endswith("h"))
        self.assertTrue(L.ago(time.time() - 200000).endswith("d"))

    def test_latest_includes_current_session(self):
        from cccopilot import cli

        root = tempfile.mkdtemp()
        old_root = L.projects_root
        old_self = os.environ.get("CLAUDE_SESSION_ID")
        cwd = "/tmp/cc-copilot-latest-test"
        d = os.path.join(root, L.encode_cwd(cwd))
        os.makedirs(d)
        current = os.path.join(d, "newest.jsonl")
        other = os.path.join(d, "older.jsonl")
        try:
            L.projects_root = lambda: root
            for p in (other, current):
                with open(p, "w", encoding="utf-8") as f:
                    f.write("{}\n")
            os.utime(other, (1000, 1000))
            os.utime(current, (2000, 2000))
            os.environ["CLAUDE_SESSION_ID"] = "newest"

            self.assertEqual(L.resolve(cwd), other)
            self.assertEqual(L.resolve(cwd, include_current=True), current)
            args = types.SimpleNamespace(cwd=cwd, session=None, latest=True, cmd="brief")
            self.assertEqual(cli._resolve_or_die(args), current)
        finally:
            L.projects_root = old_root
            if old_self is None:
                os.environ.pop("CLAUDE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_SESSION_ID"] = old_self
            shutil.rmtree(root)

    def test_session_refs_include_titles(self):
        root = tempfile.mkdtemp()
        old_root = L.projects_root
        cwd = "/tmp/cc-copilot-title-test"
        d = os.path.join(root, L.encode_cwd(cwd))
        os.makedirs(d)
        path = os.path.join(d, "sess-title.jsonl")
        try:
            L.projects_root = lambda: root
            _jsonl_lines(path, [
                {"type": "user", "cwd": cwd, "message": {"role": "user", "content": "hi"}},
                {"type": "custom-title", "customTitle": "test-session-A"},
            ])
            refs = L.list_sessions(cwd)
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0].title, "test-session-A")
        finally:
            L.projects_root = old_root
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
