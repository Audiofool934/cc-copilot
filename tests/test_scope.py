import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import re

from cccopilot import chat as C, scope as SC, state as S, transcript as T, narrate as N
from tests.util import user, asst, tool, result, iso


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


class TestCandidateRefsCrossBucket(unittest.TestCase):
    """A new same-cwd Claude session must be discovered even when it lands in a
    different ~/.claude/projects/<bucket>/ than dirname(anchor) — e.g. the macOS
    /tmp -> /private/tmp symlink (anchor pinned via the logical path, agent
    records the physical cwd), or a session started from a subdirectory."""

    _ENV = ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
            "CODEX_THREAD_ID", "CODEX_SESSION_ID")

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self._ENV}
        self.home = tempfile.mkdtemp(prefix="ccxbucket-")
        os.environ["CLAUDE_CONFIG_DIR"] = self.home

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def _sess(self, bucket_cwd, recorded_cwd, sid, ago):
        d = os.path.join(self.home, "projects",
                         re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(bucket_cwd)))
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, sid + ".jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "sessionId": sid,
                                "cwd": recorded_cwd, "timestamp": iso(ago),
                                "message": {"role": "user", "content": "hi"}}) + "\n")
        return p

    def test_new_session_in_a_different_bucket_is_still_discovered(self):
        # anchor A is on disk in the logical bucket but records the physical cwd
        a = self._sess("/tmp/proj", "/private/tmp/proj", "sessA", 600)
        # B lands in the physical-cwd bucket — only reachable via the cwd lookup
        self._sess("/private/tmp/proj", "/private/tmp/proj", "sessB", 60)
        names = {os.path.basename(r.path) for r in SC._candidate_refs(a)}
        self.assertIn("sessA.jsonl", names)
        self.assertIn("sessB.jsonl", names)   # was dropped by the agent-based skip

    def test_same_bucket_has_no_duplicates(self):
        a = self._sess("/private/tmp/p2", "/private/tmp/p2", "a2", 600)
        self._sess("/private/tmp/p2", "/private/tmp/p2", "b2", 60)
        names = [os.path.basename(r.path) for r in SC._candidate_refs(a)]
        self.assertEqual(sorted(names), ["a2.jsonl", "b2.jsonl"])
        self.assertEqual(len(names), len(set(names)))


class TestProjectScanBudget(unittest.TestCase):
    """`_text_files` must bound its walk by work done, not just files collected.

    The cockpit builds project facts on every chat message by walking the anchor
    session's cwd. From a broad parent dir whose siblings hold large non-code
    subtrees (ML data/checkpoints), reaching `max_files` text files means
    scandir-ing a huge tree — a multi-second per-message stall. The walk is capped
    by entries visited and wall-clock so that can't happen.
    """

    def _tree(self):
        root = tempfile.mkdtemp(prefix="ccscan-")
        # A data-heavy subdir that SORTS FIRST, full of binary blobs (null bytes →
        # not text), and the real code in a subdir that sorts later.
        data = os.path.join(root, "aaa_data")
        os.makedirs(data)
        for i in range(800):
            with open(os.path.join(data, f"blob{i:04d}.bin"), "wb") as f:
                f.write(b"\0\0\0")
        code = os.path.join(root, "zzz_code")
        os.makedirs(code)
        for i in range(5):
            with open(os.path.join(code, f"mod{i}.py"), "w") as f:
                f.write("print('hi')\n")
        return root, code

    def test_entry_cap_stops_before_traversing_a_huge_subtree(self):
        root, _code = self._tree()
        # cap below the data dir's file count → the walk returns before it ever
        # reaches the (later-sorting) code files. Bounded, doesn't hang.
        # use_git=False forces the filesystem-walk fallback (the temp dir is not
        # a git repo anyway, but be explicit so the test exercises the walk).
        files = SC._text_files(root, max_files=120, max_entries=200,
                               time_budget=0, use_git=False)
        self.assertEqual(files, [])

    def test_finds_text_files_when_the_budget_is_generous(self):
        root, _code = self._tree()
        files = SC._text_files(root, max_files=120, max_entries=100000,
                               time_budget=0, use_git=False)
        rels = {rel for rel, _p in files}
        self.assertTrue(any(r.endswith("mod0.py") for r in rels))

    def test_scandir_walk_bails_inside_one_giant_flat_directory(self):
        # Codex P2: a single directory holding most files must not be fully
        # listed/sorted before the budget applies. 2000 blobs in ONE dir, cap 300.
        root = tempfile.mkdtemp(prefix="ccscan-flat-")
        for i in range(2000):
            with open(os.path.join(root, f"blob{i:04d}.bin"), "wb") as f:
                f.write(b"\0\0\0")
        with open(os.path.join(root, "zzz_keep.py"), "w") as f:
            f.write("x = 1\n")
        start = time.monotonic()
        files = SC._text_files(root, max_files=120, max_entries=300,
                               time_budget=0, use_git=False)
        # bailed at the entry cap before reaching the (last-sorting) .py file
        self.assertEqual(files, [])
        self.assertLess(time.monotonic() - start, 1.0)

    def test_max_files_still_caps_collected_text_files(self):
        root = tempfile.mkdtemp(prefix="ccscan-cap-")
        for i in range(10):
            with open(os.path.join(root, f"f{i}.py"), "w") as f:
                f.write("x = 1\n")
        files = SC._text_files(root, max_files=3, max_entries=100000,
                               time_budget=0, use_git=False)
        self.assertEqual(len(files), 3)

    def test_time_budget_bounds_a_pathological_walk(self):
        root, _code = self._tree()
        start = time.monotonic()
        # tiny clock budget → returns near-immediately regardless of tree size
        SC._text_files(root, max_files=120, max_entries=10**9,
                       time_budget=0.001, use_git=False)
        self.assertLess(time.monotonic() - start, 1.0)

    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_respects_gitignore_via_git_listing(self):
        # The primary path: in a git work tree, .gitignore'd data is never walked.
        root = tempfile.mkdtemp(prefix="ccscan-git-")
        subprocess.run(["git", "-C", root, "init", "-q"],
                       check=True, stdin=subprocess.DEVNULL)
        with open(os.path.join(root, ".gitignore"), "w") as f:
            f.write("data/\n")
        os.makedirs(os.path.join(root, "data"))
        for i in range(50):
            with open(os.path.join(root, "data", f"blob{i}.bin"), "wb") as f:
                f.write(b"\0\0\0")
        with open(os.path.join(root, "keep.py"), "w") as f:
            f.write("x = 1\n")
        rels = {rel for rel, _p in SC._text_files(root, max_files=120)}
        self.assertIn("keep.py", rels)                       # tracked-able source
        self.assertNotIn(os.path.join("data", "blob.bin"), rels)  # ignored, unseen
        self.assertFalse(any(r.startswith("data" + os.sep) for r in rels))

    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_git_listed_but_deleted_files_are_skipped(self):
        # `git ls-files --cached` lists tracked files even after they're deleted
        # from the work tree — those must not enter the index as phantom evidence.
        root = tempfile.mkdtemp(prefix="ccscan-del-")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

        def g(*a):
            subprocess.run(["git", "-C", root, *a], check=True, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True)

        g("init", "-q")
        for name in ("kept.py", "gone.py"):
            with open(os.path.join(root, name), "w") as f:
                f.write("x = 1\n")
        g("add", "-A")
        g("commit", "-qm", "init")
        os.remove(os.path.join(root, "gone.py"))          # tracked but now deleted
        rels = {rel for rel, _ in SC._text_files(root, max_files=120)}
        self.assertIn("kept.py", rels)
        self.assertNotIn("gone.py", rels)

    def test_git_listing_returns_none_outside_a_repo(self):
        # a non-repo temp dir → None, so _text_files falls back to the fs walk
        outside = tempfile.mkdtemp(prefix="ccscan-norepo-")
        self.assertIsNone(SC._git_ls(outside, 100, 1.5, "--cached"))
        self.assertIsNone(SC._git_text_files(outside, 120, 100, 1.5))

    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_git_path_prefers_tracked_and_skips_others_walk_when_satisfied(self):
        # tracked files alone satisfy max_files → the slow `--others` worktree walk
        # is never run (P2: the per-chat-turn git path must be bounded).
        root = tempfile.mkdtemp(prefix="ccscan-tracked-")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

        def g(*a):
            subprocess.run(["git", "-C", root, *a], check=True, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True)

        g("init", "-q")
        for i in range(4):
            with open(os.path.join(root, f"mod{i}.py"), "w") as f:
                f.write("x = 1\n")
        g("add", "-A")
        g("commit", "-qm", "init")
        with open(os.path.join(root, "untracked.py"), "w") as f:
            f.write("y = 2\n")
        calls = []
        real = SC._git_ls

        def spy(r, limit, time_budget, *flags):
            calls.append(flags)
            return real(r, limit, time_budget, *flags)

        with mock.patch.object(SC, "_git_ls", side_effect=spy):
            rels = {rel for rel, _ in SC._text_files(root, max_files=2)}
        self.assertEqual(len(rels), 2)                        # satisfied from tracked
        self.assertNotIn(("--others", "--exclude-standard"), calls)  # walk skipped

    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_git_ls_is_bounded_by_limit(self):
        # streamed: `_git_ls` stops at `limit` and kills git, so the git path is
        # count-bounded (not buffer-the-whole-monorepo).
        root = tempfile.mkdtemp(prefix="ccscan-gitlimit-")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

        def g(*a):
            subprocess.run(["git", "-C", root, *a], check=True, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True)

        g("init", "-q")
        for i in range(20):
            with open(os.path.join(root, f"f{i:02d}.py"), "w") as f:
                f.write("x = 1\n")
        g("add", "-A")
        g("commit", "-qm", "init")
        self.assertEqual(len(SC._git_ls(root, 5, 1.5, "--cached")), 5)   # capped
        self.assertEqual(len(SC._git_ls(root, 100, 1.5, "--cached")), 20)  # all

    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_git_path_falls_to_others_when_tracked_insufficient(self):
        root = tempfile.mkdtemp(prefix="ccscan-others-")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

        def g(*a):
            subprocess.run(["git", "-C", root, *a], check=True, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True)

        g("init", "-q")                                       # nothing committed yet
        with open(os.path.join(root, "fresh.py"), "w") as f:
            f.write("z = 3\n")
        rels = {rel for rel, _ in SC._text_files(root, max_files=120)}
        self.assertIn("fresh.py", rels)                      # untracked-unignored found

    def test_filter_text_files_respects_deadline(self):
        # a past deadline → bail before stat/sniffing any candidate (so a repo of
        # many tracked binary blobs can't stall the per-turn build).
        names = [f"f{i}.bin" for i in range(1000)]
        out = SC._filter_text_files(names, "/nonexistent-root-xyz", 120,
                                    deadline=time.monotonic() - 1)
        self.assertEqual(out, [])

    def test_collect_dir_text_respects_deadline(self):
        files = [(f"f{i}.bin", f"/x/f{i}.bin") for i in range(100)]
        out = []
        hit = SC._collect_dir_text(files, "/x", out, 120,
                                   deadline=time.monotonic() - 1)
        self.assertTrue(hit)        # signals the walk to stop
        self.assertEqual(out, [])   # opened/sniffed nothing

    def test_env_overrides_are_read(self):
        with mock.patch.dict(os.environ,
                             {"CC_COPILOT_PROJECT_SCAN_MAX_ENTRIES": "777"}):
            self.assertEqual(SC._scan_int_env("CC_COPILOT_PROJECT_SCAN_MAX_ENTRIES", 1), 777)
        with mock.patch.dict(os.environ,
                             {"CC_COPILOT_PROJECT_SCAN_TIME_BUDGET": "0.25"}):
            self.assertEqual(
                SC._scan_float_env("CC_COPILOT_PROJECT_SCAN_TIME_BUDGET", 9.0), 0.25)


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
