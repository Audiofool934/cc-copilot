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
        self.assertIn("PYTHONPATH=", sh)
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
        self.assertEqual(seen["argv"], ["claude", "--resume"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][:3], ["tmux", "split-window", "-h"])

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
    def test_skips_stale_then_attaches_to_fresh(self):
        with tempfile.TemporaryDirectory() as td:
            stale = os.path.join(td, "old.jsonl")
            fresh = os.path.join(td, "new.jsonl")
            for p in (stale, fresh):
                with open(p, "w") as f:
                    f.write("{}\n")
            past = time.time() - 120
            os.utime(stale, (past, past))
            answers = iter([None, stale, fresh])
            with mock.patch.object(cli.SRC, "resolve",
                                   side_effect=lambda *a, **k: next(answers)), \
                 mock.patch.object(time, "sleep", lambda s: None), \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                got = cli._wait_for_next_session(td, slack=15.0, poll=0)
            self.assertEqual(got, fresh)
            self.assertIn("waiting for an agent session", err.getvalue())


if __name__ == "__main__":
    unittest.main()
