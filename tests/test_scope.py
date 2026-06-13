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


class _NoAmbientSession(unittest.TestCase):
    """Discovery includes the human's current session (CLAUDE_CODE_SESSION_ID).
    Running the suite from inside a live Claude session would inject that real
    session into temp-project fixtures, so neutralize it for hermetic counts."""
    def setUp(self):
        self._sess_env = {k: os.environ.pop(k, None)
                          for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")}

    def tearDown(self):
        for k, v in self._sess_env.items():
            if v is not None:
                os.environ[k] = v


class TestScopeEvidence(_NoAmbientSession):
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

    def test_project_brief_excludes_common_secret_files(self):
        cwd, a, _b = self._project_sessions()
        with open(os.path.join(cwd, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Safe Project\n")
        secret_files = {
            ".npmrc": "//registry.npmjs.org/:_authToken=npm_secret_token\n",
            ".pypirc": "password = pypi_secret_token\n",
            ".netrc": "machine example.com password netrc_secret_token\n",
            "service-account.json": '{"private_key": "service_secret_token"}\n',
            "deploy.secret": "deploy_secret_token\n",
        }
        for rel, text in secret_files.items():
            with open(os.path.join(cwd, rel), "w", encoding="utf-8") as f:
                f.write(text)
        st = S.build(T.parse(a))

        brief = SC.render_evidence(a, st, "project").text

        self.assertIn("Safe Project", brief)
        for rel, text in secret_files.items():
            self.assertNotIn(rel, brief)
            self.assertNotIn(text.strip(), brief)

    def test_missing_session_selector_raises(self):
        _cwd, a, _b = self._project_sessions()
        st = S.build(T.parse(a))
        self.assertRaises(ValueError, SC.render_evidence, a, st, "multi", sessions=["nope"])

    def test_unicode_digit_selector_is_a_clean_miss_not_a_value_error(self):
        # '②' satisfies str.isdigit() but int() would raise; it must fall through
        # to the normal "no session matching" path, not a stray int() ValueError.
        _cwd, a, _b = self._project_sessions()
        st = S.build(T.parse(a))
        with self.assertRaises(ValueError) as cm:
            SC.render_evidence(a, st, "multi", sessions=["②"])
        self.assertIn("no session matching", str(cm.exception))
        self.assertIn("②", str(cm.exception))


class TestSkipFile(unittest.TestCase):
    """Unit-level contract for the secret/dir filter so the broad-match
    regressions (source files dropped) and false-negatives (secrets kept) the
    adversarial review found stay fixed."""

    def test_secret_files_are_skipped(self):
        for name in (".env", ".env.prod", ".npmrc", ".pgpass", ".htpasswd",
                     ".dockercfg", ".dockerconfigjson", "id_rsa",
                     "credentials.yaml", "secrets.yaml", "secrets.yml",
                     "token.json", "auth.json", "terraform.tfvars",
                     "service-account.json", "service_account.json",
                     "firebase-adminsdk-ab12c.json", "my-service-account.json",
                     "release.keystore", "store.jks", "prod.token",
                     ".bash_history", ".zsh_history", ".psql_history"):
            self.assertTrue(SC._skip_file(name, name), f"should skip {name}")

    def test_legitimate_source_is_kept(self):
        # The substring-fragment and basename-prefix matches used to drop these.
        for name in ("README.md", "secrets.py", "secrets.md", "secrets.go",
                     "credentials.py", "credentials.go", "service_account.go",
                     "service_account_test.py", "service-account-controller.ts",
                     "application_default_credentials_test.go", "tokenizer.py"):
            self.assertFalse(SC._skip_file(name, name), f"should keep {name}")

    def test_fragment_match_is_gated_on_json(self):
        # The varying-basename credential blob is still caught when it is JSON…
        self.assertTrue(SC._skip_file("firebase-adminsdk-xx.json",
                                      "firebase-adminsdk-xx.json"))
        # …but the same fragment in a source file name is not.
        self.assertFalse(SC._skip_file("service_account.go", "service_account.go"))

    def test_secret_dir_anywhere_in_path_is_skipped(self):
        self.assertTrue(SC._skip_file("config", os.path.join(".ssh", "config")))
        self.assertTrue(SC._skip_file("note.txt", os.path.join("a", ".aws", "note.txt")))


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

    def test_chat_session_includes_project_context_by_default(self):
        cwd = tempfile.mkdtemp(prefix="ccscope-chat-default-project-")
        d = tempfile.mkdtemp(prefix="ccscope-chat-default-sessions-")
        p = os.path.join(d, "sess-A.jsonl")
        _write_at(p, [user("inspect project", 60, sessionId="sess-A", cwd=cwd), asst("ok", 5)])
        with open(os.path.join(cwd, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Always On Project\n")
        seen = []
        real = N.chat_brief
        N.chat_brief = lambda brief, history, q, model=None, backend=None: seen.append(brief) or "answer"
        try:
            s = C.ChatSession(p, alerts=False, persist=False)
            self.assertEqual(s.answer("what project context do you have?"), "answer")
        finally:
            N.chat_brief = real
        self.assertIn("Always On Project", seen[0])
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
        self.assertEqual(args.poll, 2)
        args = cli.build_parser().parse_args(["brief", "--scope", "repo"])
        self.assertEqual(args.scope, SC.PROJECT)
        args = cli.build_parser().parse_args(
            ["brief", "--scope", "multi", "--scope-sessions", "sess-A,sess-B"])
        self.assertEqual(SC.parse_selectors(args.scope_sessions), ["sess-A", "sess-B"])


class TestSameFile(unittest.TestCase):
    def test_same_file_tolerates_a_missing_path(self):
        # history-only mode: the observed transcript can vanish while the cockpit
        # keeps its path, so samefile would raise — the helper must degrade.
        fd, real = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        try:
            self.assertTrue(C._same_file(real, real))
            self.assertFalse(C._same_file(real, "/no/such/path-xyz.jsonl"))
            self.assertFalse(C._same_file("/gone-a.jsonl", "/gone-b.jsonl"))
        finally:
            os.unlink(real)


if __name__ == "__main__":
    unittest.main()
