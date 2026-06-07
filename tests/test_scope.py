import json
import os
import tempfile
import unittest

from cccopilot import chat as C, scope as SC, state as S, transcript as T, narrate as N
from tests.util import user, asst, tool, result


def _write_at(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


class TestScopeEvidence(unittest.TestCase):
    def _project_sessions(self):
        cwd = tempfile.mkdtemp(prefix="ccscope-proj-")
        d = tempfile.mkdtemp(prefix="ccscope-sessions-")
        a = os.path.join(d, "sess-A.jsonl")
        b = os.path.join(d, "sess-B.jsonl")
        _write_at(a, [
            user("fix tests", 120, sessionId="sess-A", cwd=cwd),
            tool("Bash", {"command": "pytest"}, "t1", 60),
            result("t1", "failed", is_error=True, ago=59),
        ])
        _write_at(b, [
            user("update docs", 120, sessionId="sess-B", cwd=cwd),
            asst("done", 10),
        ])
        return cwd, a, b

    def test_scope_aliases(self):
        self.assertEqual(SC.normalize("session"), SC.SESSION)
        self.assertEqual(SC.normalize("multi"), SC.MULTI)
        self.assertEqual(SC.normalize("repo"), SC.PROJECT)
        self.assertRaises(ValueError, SC.normalize, "internet")

    def test_multi_session_brief_uses_session_qualified_citations(self):
        _cwd, a, _b = self._project_sessions()
        st = S.build(T.parse(a))

        brief = SC.render_evidence(a, st, "multi-session").text

        self.assertIn("scope `multi-session`", brief)
        self.assertIn("sess-A", brief)
        self.assertIn("sess-B", brief)
        self.assertIn("[sess-A:L", brief)
        self.assertIn("Bash failed", brief)

    def test_multi_session_brief_can_select_specific_sessions(self):
        _cwd, a, _b = self._project_sessions()
        st = S.build(T.parse(a))

        brief = SC.render_evidence(a, st, "multi-session", sessions=["sess-B"]).text

        self.assertIn("1 selected of 2 work-session", brief)
        self.assertIn("sess-B", brief)
        self.assertNotIn("sess-A`", brief)
        self.assertNotIn("Bash failed", brief)

    def test_project_brief_includes_read_only_file_citations(self):
        cwd, a, _b = self._project_sessions()
        with open(os.path.join(cwd, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Project Title\n\nRead-only evidence.\n")
        st = S.build(T.parse(a))

        brief = SC.render_evidence(a, st, "project").text

        self.assertIn("project facts", brief)
        self.assertIn("[README.md:L1]", brief)
        self.assertIn("Project Title", brief)
        self.assertIn("[tree]", brief)

    def test_missing_session_selector_raises(self):
        _cwd, a, _b = self._project_sessions()
        st = S.build(T.parse(a))
        self.assertRaises(ValueError, SC.render_evidence, a, st, "multi", sessions=["nope"])


class TestChatScope(unittest.TestCase):
    def test_chat_session_answers_from_project_scope_brief(self):
        cwd = tempfile.mkdtemp(prefix="ccscope-chat-")
        d = tempfile.mkdtemp(prefix="ccscope-chat-sessions-")
        p = os.path.join(d, "sess-A.jsonl")
        _write_at(p, [user("inspect project", 60, sessionId="sess-A", cwd=cwd), asst("ok", 5)])
        with open(os.path.join(cwd, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Scoped Project\n")
        seen = []
        real = N.chat_brief
        N.chat_brief = lambda brief, history, q, model=None, backend=None: seen.append(brief) or "answer"
        try:
            s = C.ChatSession(p, alerts=False, persist=False, scope="project")
            self.assertEqual(s.answer("what can you see?"), "answer")
        finally:
            N.chat_brief = real
        self.assertTrue(seen)
        self.assertIn("Scoped Project", seen[0])
        self.assertIn("[README.md:L1]", seen[0])

    def test_chat_session_answers_from_selected_multi_session_brief(self):
        cwd = tempfile.mkdtemp(prefix="ccscope-chat-multi-")
        d = tempfile.mkdtemp(prefix="ccscope-chat-multi-sessions-")
        a = _write_at(os.path.join(d, "sess-A.jsonl"),
                      [user("fix tests", 60, sessionId="sess-A", cwd=cwd), asst("ok", 5)])
        _write_at(os.path.join(d, "sess-B.jsonl"),
                  [user("write docs", 60, sessionId="sess-B", cwd=cwd), asst("done", 5)])
        seen = []
        real = N.chat_brief
        N.chat_brief = lambda brief, history, q, model=None, backend=None: seen.append(brief) or "answer"
        try:
            s = C.ChatSession(a, alerts=False, persist=False,
                              scope="multi", scope_sessions=["sess-B"])
            self.assertEqual(s.scope_label(), "multi-session:1")
            self.assertEqual(s.answer("summarize selected"), "answer")
        finally:
            N.chat_brief = real
        self.assertIn("sess-B", seen[0])
        self.assertNotIn("sess-A`", seen[0])


class TestScopeCli(unittest.TestCase):
    def test_scope_aliases_parse_to_canonical_values(self):
        from cccopilot import cli
        args = cli.build_parser().parse_args(["chat", "--scope", "multi"])
        self.assertEqual(args.scope, SC.MULTI)
        args = cli.build_parser().parse_args(["brief", "--scope", "repo"])
        self.assertEqual(args.scope, SC.PROJECT)
        args = cli.build_parser().parse_args(
            ["brief", "--scope", "multi", "--scope-sessions", "sess-A,sess-B"])
        self.assertEqual(SC.parse_selectors(args.scope_sessions), ["sess-A", "sess-B"])


if __name__ == "__main__":
    unittest.main()
