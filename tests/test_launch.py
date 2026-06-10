"""`cc-copilot launch` and the bare-invocation cockpit default."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from cccopilot import cli


class TestEffectiveArgv(unittest.TestCase):
    def test_bare_on_a_tty_means_cockpit(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
             mock.patch.object(sys.stdout, "isatty", return_value=True):
            self.assertEqual(cli._effective_argv([]), ["cockpit"])

    def test_bare_off_tty_keeps_the_usage_error(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            self.assertEqual(cli._effective_argv([]), [])

    def test_real_args_pass_through_untouched(self):
        self.assertEqual(cli._effective_argv(["sessions"]), ["sessions"])
        self.assertEqual(cli._effective_argv(["--version"]), ["--version"])

    def test_stdout_piped_keeps_the_usage_error(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
             mock.patch.object(sys.stdout, "isatty", return_value=False):
            self.assertEqual(cli._effective_argv([]), [])

    def test_closed_stdin_does_not_crash(self):
        # daemons may close (not null) stdio: Python then sets sys.stdin = None
        with mock.patch.object(sys, "stdin", None):
            self.assertEqual(cli._effective_argv([]), [])


class TestLaunchParser(unittest.TestCase):
    def test_launch_subcommand_exists_with_alias(self):
        for name in ("launch", "up"):
            args = cli.build_parser().parse_args([name])
            self.assertIs(args.func, cli.cmd_launch)

    def test_cockpit_grew_a_next_flag(self):
        args = cli.build_parser().parse_args(["cockpit", "--next"])
        self.assertTrue(args.next)
        args = cli.build_parser().parse_args(["cockpit"])
        self.assertFalse(args.next)


class TestLaunchPlan(unittest.TestCase):
    def test_inside_tmux_splits_then_execs_the_agent(self):
        setup, final = cli._launch_plan(["claude", "--resume"], "/tmp/p", "COCKPIT", True)
        self.assertEqual(final, ["claude", "--resume"])
        self.assertEqual(setup, [["tmux", "split-window", "-h", "-d",
                                  "-c", "/tmp/p", "COCKPIT"]])

    def test_outside_tmux_builds_a_session_and_attaches(self):
        setup, final = cli._launch_plan(["codex"], "/tmp/p", "COCKPIT",
                                        False, "cc-copilot-2")
        self.assertEqual(final, ["tmux", "attach-session", "-t", "cc-copilot-2"])
        self.assertEqual(setup[0][:5], ["tmux", "new-session", "-d", "-s", "cc-copilot-2"])
        self.assertEqual(setup[0][-1], "codex")
        self.assertEqual(setup[1][-1], "COCKPIT")
        self.assertIn("-t", setup[1])

    def test_agent_args_are_shell_quoted(self):
        setup, _ = cli._launch_plan(["claude", "--add-dir", "/tmp/with space"],
                                    "/p", "C", False)
        self.assertIn("'/tmp/with space'", setup[0][-1])


class TestFreeTmuxSession(unittest.TestCase):
    def test_skips_taken_names(self):
        taken = {"cc-copilot", "cc-copilot-2"}
        self.assertEqual(cli._free_tmux_session(lambda n: n in taken), "cc-copilot-3")

    def test_default_name_when_free(self):
        self.assertEqual(cli._free_tmux_session(lambda n: False), "cc-copilot")


class TestCockpitSh(unittest.TestCase):
    def test_installed_entry_point_is_absolute(self):
        with mock.patch("shutil.which", return_value="/abs/bin/cc-copilot"), \
             mock.patch.object(cli, "_is_source_checkout", return_value=False):
            sh = cli._cockpit_sh("/tmp/p")
        self.assertTrue(sh.startswith("/abs/bin/cc-copilot "))
        self.assertIn("cockpit --next --cwd /tmp/p", sh)

    def test_source_checkout_relaunches_itself_with_pythonpath(self):
        # Even with an older cc-copilot installed, a checkout must spawn its
        # own code in the cockpit pane — not whatever `which` finds.
        with mock.patch("shutil.which", return_value="/abs/bin/cc-copilot"), \
             mock.patch.object(cli, "_is_source_checkout", return_value=True):
            sh = cli._cockpit_sh("/tmp/p")
        # `env` prefix: tmux runs this via the default-shell, which may be
        # fish/tcsh — bare VAR=… prefixes are POSIX-only syntax there.
        self.assertTrue(sh.startswith("env PYTHONPATH="))
        self.assertIn("-m cccopilot cockpit --next", sh)
        self.assertNotIn("/abs/bin/cc-copilot", sh)


class TestCmdLaunch(unittest.TestCase):
    def _args(self, argv):
        return cli.build_parser().parse_args(argv)

    def test_no_tmux_falls_back_to_cockpit_only(self):
        seen = {}

        def fake_execvpe(prog, argv, env):
            seen["argv"] = argv
            raise SystemExit(0)

        which = lambda c: None if c == "tmux" else f"/bin/{c}"
        with tempfile.TemporaryDirectory() as td, \
             mock.patch("shutil.which", side_effect=which), \
             mock.patch.object(os, "execvpe", fake_execvpe), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit):
                cli.cmd_launch(self._args(["launch", "--cwd", td, "claude"]))
        self.assertIn("cockpit", seen["argv"])
        self.assertNotIn("--next", seen["argv"])   # no agent started: don't wait
        # physical path: agents record realpath'd cwds (macOS /tmp, /var …)
        self.assertIn(os.path.realpath(td), seen["argv"])
        self.assertIn("tmux not found", err.getvalue())

    def test_bad_cwd_is_a_clean_error(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = cli.cmd_launch(self._args(["launch", "--cwd", "/nope/nowhere"]))
        self.assertEqual(rc, 2)
        self.assertIn("no such directory", err.getvalue())

    def test_new_session_failure_does_not_kill_someone_elses_session(self):
        # Duplicate-name race: if new-session itself fails, the session
        # belongs to a concurrent launch — never kill it.
        runs = []

        def fake_run(argv, **kw):
            runs.append(argv)
            # has-session: 1 = name free; new-session: 1 = the race loss
            rc = 1 if argv[1] in ("has-session", "new-session") else 0
            return types.SimpleNamespace(returncode=rc)

        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        with tempfile.TemporaryDirectory() as td, \
             mock.patch("shutil.which", side_effect=lambda c: f"/bin/{c}"), \
             mock.patch.object(subprocess, "run", fake_run), \
             mock.patch.dict(os.environ, env, clear=True), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            rc = cli.cmd_launch(self._args(["launch", "--cwd", td, "claude"]))
        self.assertEqual(rc, 1)
        self.assertNotIn("kill-session", [a[1] for a in runs])
        self.assertIn("failed", err.getvalue())

    def test_inside_tmux_splits_and_execs_agent(self):
        runs, seen = [], {}

        def fake_run(argv, **kw):
            runs.append(argv)
            return types.SimpleNamespace(returncode=0)

        def fake_execvp(prog, argv):
            seen["argv"] = argv
            raise SystemExit(0)

        prev = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as td, \
                 mock.patch("shutil.which", side_effect=lambda c: f"/bin/{c}"), \
                 mock.patch.object(subprocess, "run", fake_run), \
                 mock.patch.dict(os.environ, {"TMUX": "/tmp/sock,1,0"}), \
                 mock.patch.object(os, "execvp", fake_execvp):
                with self.assertRaises(SystemExit):
                    cli.cmd_launch(self._args(
                        ["launch", "--cwd", td, "--", "claude", "--resume"]))
        finally:
            os.chdir(prev)   # cmd_launch chdirs before the (faked) exec
        # agent binary absolutized: the tmux server's PATH, not ours, resolves
        # pane commands, and execvp should agree with the preflight which()
        self.assertEqual(seen["argv"], ["/bin/claude", "--resume"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][:3], ["tmux", "split-window", "-h"])

    def test_missing_tui_extra_aborts_before_any_pane(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch("shutil.which", side_effect=lambda c: f"/bin/{c}"), \
             mock.patch.object(cli, "_tui_importable", return_value=False), \
             mock.patch.object(cli, "_is_source_checkout", return_value=False), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            rc = cli.cmd_launch(self._args(["launch", "--cwd", td]))
        self.assertEqual(rc, 3)
        self.assertIn("[tui]", err.getvalue())

    def test_unknown_agent_is_a_clean_error(self):
        which = lambda c: "/bin/tmux" if c == "tmux" else None
        with mock.patch("shutil.which", side_effect=which), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            rc = cli.cmd_launch(self._args(["launch", "nonesuch-agent"]))
        self.assertEqual(rc, 2)
        self.assertIn("not found on PATH", err.getvalue())

    def test_no_agent_anywhere_is_a_clean_error(self):
        with mock.patch("shutil.which", return_value=None), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            rc = cli.cmd_launch(self._args(["launch"]))
        self.assertEqual(rc, 2)
        self.assertIn("no agent found", err.getvalue())


class TestWaitForNextSession(unittest.TestCase):
    """`--next` waits for the session picture to CHANGE — recency alone can't
    tell the just-launched agent from a transcript quit seconds ago."""

    def _resolve(self, td, answers):
        """A SRC.resolve fake that also pins the call contract."""
        def fake(cwd_arg, session_arg=None, **kw):
            self.assertEqual(cwd_arg, td)
            self.assertIsNone(session_arg)
            return answers()
        return fake

    def _touch(self, path, ago=0.0):
        with open(path, "a") as f:
            f.write("{}\n")
        t = time.time() - ago
        os.utime(path, (t, t))

    def test_fresh_project_attaches_to_first_session(self):
        with tempfile.TemporaryDirectory() as td:
            fresh = os.path.join(td, "new.jsonl")
            self._touch(fresh)
            seq = iter([None, None, fresh])
            with mock.patch.object(cli.SRC, "resolve",
                                   side_effect=self._resolve(td, lambda: next(seq))), \
                 mock.patch.object(time, "sleep", lambda s: None), \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                got = cli._wait_for_next_session(td, poll=0)
            self.assertEqual(got, fresh)
            self.assertIn("waiting for an agent session", err.getvalue())

    def test_does_not_pin_a_just_quit_session(self):
        # The death of the 15s-recency design: a transcript the user quit
        # moments before `launch` is recent, but it is not the next session.
        with tempfile.TemporaryDirectory() as td:
            quit_ = os.path.join(td, "quit.jsonl")
            fresh = os.path.join(td, "new.jsonl")
            self._touch(quit_, ago=2.0)   # would beat any slack window
            self._touch(fresh)
            seq = iter([quit_, quit_, quit_, fresh])
            with mock.patch.object(cli.SRC, "resolve",
                                   side_effect=self._resolve(td, lambda: next(seq))), \
                 mock.patch.object(time, "sleep", lambda s: None), \
                 contextlib.redirect_stderr(io.StringIO()):
                got = cli._wait_for_next_session(td, poll=0)
            self.assertEqual(got, fresh)

    def test_resume_appending_to_an_old_transcript_counts(self):
        # `claude --resume` reuses a transcript: same path, growing mtime.
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.jsonl")
            self._touch(p, ago=120.0)
            calls = {"n": 0}

            def answers():
                calls["n"] += 1
                if calls["n"] == 3:        # the resume lands
                    self._touch(p)
                return p

            with mock.patch.object(cli.SRC, "resolve",
                                   side_effect=self._resolve(td, answers)), \
                 mock.patch.object(time, "sleep", lambda s: None), \
                 contextlib.redirect_stderr(io.StringIO()):
                got = cli._wait_for_next_session(td, poll=0)
            self.assertEqual(got, p)
            self.assertGreaterEqual(calls["n"], 3)


class TestCmdChatNext(unittest.TestCase):
    def _args(self, argv):
        return cli.build_parser().parse_args(argv)

    def test_ctrl_c_while_waiting_exits_130(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(cli, "_tui_importable", return_value=True), \
             mock.patch.object(cli, "_wait_for_next_session",
                               side_effect=KeyboardInterrupt), \
             contextlib.redirect_stderr(io.StringIO()):
            rc = cli.cmd_chat(self._args(["cockpit", "--next", "--cwd", td]))
        self.assertEqual(rc, 130)

    def test_explicit_session_skips_the_wait(self):
        with tempfile.TemporaryDirectory() as td:
            sess = os.path.join(td, "s.jsonl")
            with open(sess, "w") as f:
                f.write("{}\n")
            with mock.patch.object(cli, "_tui_importable", return_value=True), \
                 mock.patch.object(cli, "_wait_for_next_session",
                                   side_effect=AssertionError("must not wait")), \
                 mock.patch("cccopilot.chat.ChatSession",
                            side_effect=ValueError("stop here")), \
                 contextlib.redirect_stderr(io.StringIO()):
                rc = cli.cmd_chat(self._args(["cockpit", "--next", sess]))
            self.assertEqual(rc, 2)   # ValueError path: got past the wait


if __name__ == "__main__":
    unittest.main()
