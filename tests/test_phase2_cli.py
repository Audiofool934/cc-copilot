"""End-to-end CLI wiring for the re-entry commands (since / handoff)."""

import contextlib
import io
import os
import tempfile
import unittest

from cccopilot import cli, lastlook as LL, sources as SRC
from tests.util import asst, result, tool, user, write


class TestSinceHandoffCli(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY")}
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="ccp2-")
        os.environ.pop("CC_COPILOT_HISTORY", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _run(self, argv):
        from cccopilot import config as CFG
        args = cli.build_parser().parse_args(argv)
        CFG.apply_defaults(args)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = args.func(args)
        return rc, buf.getvalue()

    def _session(self):
        return write([user("go", 200),
                      tool("Bash", {"command": "make build"}, "t1", 30),
                      result("t1", "ok", ago=29)])

    def test_since_first_time_records_position(self):
        p = self._session()
        rc, out = self._run(["since", "--session", p])
        self.assertEqual(rc, 0)
        self.assertIn("recorded your current position", out)

    def test_since_shows_diff_against_marker_and_advances(self):
        p = self._session()
        tr = SRC.parse(p)
        key = LL.key_for(tr.session_id or "", p)
        LL.mark(key, 1, "", "")                      # last look was before the command
        rc, out = self._run(["since", "--session", p])
        self.assertEqual(rc, 0)
        self.assertIn("since last look", out)
        self.assertIn("make build", out)             # the command after the marker
        self.assertGreater(LL.get(key)["line"], 1)   # marker advanced to the tail

    def test_since_peek_does_not_advance(self):
        p = self._session()
        tr = SRC.parse(p)
        key = LL.key_for(tr.session_id or "", p)
        LL.mark(key, 1, "", "")
        self._run(["since", "--session", p, "--peek"])
        self.assertEqual(LL.get(key)["line"], 1)     # unchanged with --peek

    def test_since_duration(self):
        p = self._session()
        rc, out = self._run(["since", "2h", "--session", p])
        self.assertEqual(rc, 0)
        self.assertIn("since 2h", out)

    def test_since_bad_duration(self):
        p = self._session()
        rc, _ = self._run(["since", "soon", "--session", p])
        self.assertEqual(rc, 2)

    def test_since_30m_is_a_window_not_a_session(self):
        # `cc-copilot since 30m` must read as a time window, not session "30m"
        args = cli.build_parser().parse_args(["since", "30m"])
        self.assertEqual(args.when, "30m")
        self.assertIsNone(args.session)

    def test_since_opt_out_message(self):
        os.environ["CC_COPILOT_HISTORY"] = "0"
        try:
            p = self._session()
            rc, out = self._run(["since", "--session", p])
            self.assertEqual(rc, 0)
            self.assertIn("last-look tracking is off", out)
        finally:
            os.environ.pop("CC_COPILOT_HISTORY", None)

    def test_handoff_to_file(self):
        p = self._session()
        out_path = os.path.join(os.environ["CC_COPILOT_STATE_DIR"], "h.md")
        rc, out = self._run(["handoff", p, "--out", out_path])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(out_path))
        with open(out_path, encoding="utf-8") as f:
            md = f.read()
        self.assertIn("# Handoff —", md)
        self.assertIn("## Full brief", md)

    def test_handoff_stdout(self):
        p = self._session()
        rc, out = self._run(["handoff", p])
        self.assertEqual(rc, 0)
        self.assertIn("# Handoff —", out)


class TestTuiRuntimeBootstrapGate(unittest.TestCase):
    """An installed (non-clone) package must NEVER write a .venv into its install
    dir (it may be read-only under uv/pipx) — it points at the [tui] extra."""

    def test_source_checkout_detected_from_repo(self):
        # the suite runs from the cloned repo (pyproject.toml + .git present)
        self.assertTrue(cli._is_source_checkout())

    def test_installed_without_textual_points_to_extra_not_bootstrap(self):
        d = tempfile.mkdtemp(prefix="ccinstall-")   # bare dir = like site-packages
        orig_root, orig_imp = cli._repo_root, cli._tui_importable
        cli._repo_root = lambda: d                  # pretend we're the installed package
        cli._tui_importable = lambda: False         # textual absent
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                vpy = cli._ensure_tui_runtime(quiet=False)
            self.assertIsNone(vpy)                              # no runtime built
            self.assertIn("cc-copilot[tui]", err.getvalue())   # directs to the extra
            self.assertFalse(os.path.isdir(os.path.join(d, ".venv")))  # nothing written
        finally:
            cli._repo_root, cli._tui_importable = orig_root, orig_imp


class TestInitCli(unittest.TestCase):
    """`cc-copilot init` writes the config; in a non-interactive run it falls
    back to a safe default (the first ready CLI / Claude) without prompting."""

    def setUp(self):
        from cccopilot import onboard as OB  # noqa: F401
        self._saved = {k: os.environ.pop(k, None) for k in
                       ("CC_COPILOT_CONFIG", "CC_COPILOT_NO_ONBOARD",
                        "CC_COPILOT_BACKEND", "CC_COPILOT_MODEL", "OPENAI_API_KEY")}
        self.dir = tempfile.mkdtemp(prefix="ccinit-")
        self.p = os.path.join(self.dir, "cc.toml")
        os.environ["CC_COPILOT_CONFIG"] = self.p
        os.environ.pop("CC_COPILOT_NO_ONBOARD", None)

    def tearDown(self):
        for k in ("CC_COPILOT_CONFIG", "CC_COPILOT_NO_ONBOARD",
                  "CC_COPILOT_BACKEND", "CC_COPILOT_MODEL", "OPENAI_API_KEY"):
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def _run(self, argv):
        args = cli.build_parser().parse_args(argv)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = args.func(args)
        return rc, out.getvalue() + err.getvalue()

    def test_init_non_interactive_writes_a_default_config(self):
        from cccopilot import onboard as OB
        rc, _ = self._run(["init"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(self.p))
        self.assertFalse(OB.needs_onboarding())          # won't ask again

    def test_init_refuses_silent_overwrite_when_config_exists(self):
        from pathlib import Path
        from cccopilot import onboard as OB
        OB.write_choice("claude")                        # pre-existing config
        before = Path(self.p).read_text()
        rc, out = self._run(["init"])                    # non-tty → no prompt, no clobber
        self.assertEqual(rc, 0)
        self.assertIn("already exists", out)
        self.assertEqual(Path(self.p).read_text(), before)   # untouched

    def test_init_force_reconfigures_existing(self):
        from cccopilot import onboard as OB
        OB.write_choice("openai", key_value="sk-keep")   # existing api key
        rc, _ = self._run(["init", "--force"])           # rewrites to the default
        self.assertEqual(rc, 0)
        from cccopilot import config as CFG
        data = CFG._load_simple(self.p)
        self.assertEqual(data["env"]["OPENAI_API_KEY"], "sk-keep")  # key preserved

    def test_terminal_wizard_propagates_choice_into_args(self):
        # a first-run plain `chat` builds the ChatSession right after the wizard;
        # the chosen backend must land on args so it's used now, not next launch.
        import argparse
        ns = argparse.Namespace(backend=None, model=None)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli._run_terminal_onboard(ns)
        self.assertEqual(rc, 0)
        self.assertEqual(ns.backend, "claude")           # first row / first ready CLI


if __name__ == "__main__":
    unittest.main()
