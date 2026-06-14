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
    def test_claude_config_dir_overrides_default_home(self):
        root = tempfile.mkdtemp()
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        try:
            os.environ["CLAUDE_CONFIG_DIR"] = root
            self.assertEqual(L.claude_home(), root)
            self.assertEqual(L.projects_root(), os.path.join(root, "projects"))
            self.assertEqual(L.sessions_root(), os.path.join(root, "sessions"))
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
            shutil.rmtree(root)

    def test_encode_cwd(self):
        self.assertEqual(L.encode_cwd("/Users/a/Projects"), "-Users-a-Projects")
        self.assertEqual(L.encode_cwd("/x/a.b.c"), "-x-a-b-c")
        self.assertEqual(L.encode_cwd("/p/My Repo"), "-p-My-Repo")

    def test_is_own_session_true_current_prompt(self):
        p = _jsonl({"type": "user", "message": {"role": "user",
                   "content": "You are cc-copilot, a read-only cockpit agent for supervising coding agents."}})
        try:
            self.assertTrue(L.is_own_session(p))
        finally:
            os.unlink(p)

    def test_is_own_session_true_legacy_prompt(self):
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
        saved = {k: os.environ.get(k) for k in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")}
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
            # the current session is identified by CLAUDE_CODE_SESSION_ID (newer
            # name); clear the legacy var so only this one is in effect
            os.environ["CLAUDE_CODE_SESSION_ID"] = "newest"
            os.environ.pop("CLAUDE_SESSION_ID", None)

            self.assertEqual(L.resolve(cwd), other)
            self.assertEqual(L.resolve(cwd, include_current=True), current)
            args = types.SimpleNamespace(cwd=cwd, session=None, latest=True, cmd="brief")
            self.assertEqual(cli._resolve_or_die(args), current)
        finally:
            L.projects_root = old_root
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
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


class TestTitlePrecedence(unittest.TestCase):
    """A name the human set (custom-title) must beat the auto-generated ai-title,
    even when Claude Code re-emits the ai-title *after* the rename."""

    def _write(self, events):
        d = tempfile.mkdtemp(prefix="cctitle-")
        p = os.path.join(d, "s.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        return p

    def test_custom_title_beats_later_ai_title(self):
        from cccopilot import transcript as T
        p = self._write([
            {"type": "custom-title", "customTitle": "dev", "sessionId": "s"},
            {"type": "ai-title", "aiTitle": "Auto Title Generated Later", "sessionId": "s"},
        ])
        self.assertEqual(L.read_title(p, "s"), "dev")
        self.assertEqual(T.parse(p).title, "dev")

    def test_ai_title_used_when_no_custom(self):
        from cccopilot import transcript as T
        p = self._write([{"type": "ai-title", "aiTitle": "Just The Auto Title", "sessionId": "s"}])
        self.assertEqual(L.read_title(p, "s"), "Just The Auto Title")
        self.assertEqual(T.parse(p).title, "Just The Auto Title")

    def test_latest_custom_title_wins(self):
        p = self._write([
            {"type": "custom-title", "customTitle": "first", "sessionId": "s"},
            {"type": "ai-title", "aiTitle": "auto", "sessionId": "s"},
            {"type": "custom-title", "customTitle": "renamed", "sessionId": "s"},
        ])
        self.assertEqual(L.read_title(p, "s"), "renamed")


class TestSessionMetaRobustness(unittest.TestCase):
    def test_non_numeric_updated_at_does_not_crash(self):
        # a sessions/*.json with an ISO-string updatedAt used to raise ValueError
        # out of int() and crash all session/scope discovery.
        d = tempfile.mkdtemp(prefix="cclocate-meta-")
        with open(os.path.join(d, "s.json"), "w") as f:
            json.dump({"sessionId": "abc123", "name": "My Session",
                       "updatedAt": "2026-06-13T10:00:00Z"}, f)
        orig = L.sessions_root
        try:
            L.sessions_root = lambda: d
            self.assertEqual(L._session_meta_name("abc123"), "My Session")
        finally:
            L.sessions_root = orig


class TestSubagentCount(unittest.TestCase):
    def test_counts_children_in_subagents_dir(self):
        d = tempfile.mkdtemp()
        try:
            sid = "46bc2aea-3dec-410a-962a-80e9c4d652b4"
            path = os.path.join(d, sid + ".jsonl")
            open(path, "w").close()
            self.assertEqual(L.subagent_count(path), 0)        # none yet
            sub = os.path.join(d, sid, "subagents")
            os.makedirs(sub)
            for name in ("agent-aaa.jsonl", "agent-bbb.jsonl", "notes.txt"):
                open(os.path.join(sub, name), "w").close()
            self.assertEqual(L.subagent_count(path), 2)        # only agent-*.jsonl
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_zero_for_non_session_paths(self):
        self.assertEqual(L.subagent_count(""), 0)
        self.assertEqual(L.subagent_count("/nope/x.txt"), 0)


if __name__ == "__main__":
    unittest.main()
