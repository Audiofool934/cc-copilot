"""The agent-source dispatcher: routing, registry, union, and config gating."""

import os
import unittest

from cccopilot import sources as SRC
from cccopilot.sources import codex as CX
from cccopilot.sources.base import AgentSource
from cccopilot.sources.claude import ClaudeSource
from tests import util as UC
from tests import util_codex as UX


class TestRegistryAndRouting(unittest.TestCase):
    def test_registered_specific_first(self):
        names = [s.name for s in SRC.all_sources()]
        self.assertIn("codex", names)
        self.assertIn("claude", names)
        self.assertLess(names.index("codex"), names.index("claude"))  # codex is more specific

    def test_source_for_codex_path(self):
        p = "/x/rollout-2026-06-07T10-00-00-abc.jsonl"
        self.assertIsInstance(SRC.source_for_path(p), CX.CodexSource)

    def test_source_for_unknown_path_defaults_to_claude(self):
        # an arbitrary temp transcript (not under either home) → Claude default
        p = UC.write([UC.user("hi", 10), UC.asst("ok", 5)])
        self.assertIsInstance(SRC.source_for_path(p), ClaudeSource)

    def test_parse_routes_to_the_right_adapter(self):
        claude_p = UC.write([UC.user("claude prompt", 10), UC.asst("done", 5)])
        codex_p = UX.write_rollout([UX.session_meta(), UX.umsg("codex prompt", 10),
                                    UX.amsg("done", 5)])
        ctr = SRC.parse(claude_p)
        xtr = SRC.parse(codex_p)
        self.assertEqual(ctr.records[0].text, "claude prompt")
        self.assertEqual(xtr.records[0].text, "codex prompt")


class TestEnabledSources(unittest.TestCase):
    """``enabled_sources`` gating. Both agents are made *available* via temp
    homes so the test exercises the env/arg filter rather than incidentally
    depending on whether ~/.claude or ~/.codex exist on the CI runner."""

    def setUp(self):
        import tempfile
        self._saved = {k: os.environ.get(k)
                       for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "CC_COPILOT_AGENTS")}
        claude_home = tempfile.mkdtemp(prefix="claude-home-")
        os.makedirs(os.path.join(claude_home, "projects"))   # → ClaudeSource.available()
        codex_home = tempfile.mkdtemp(prefix="codex-home-")
        os.makedirs(os.path.join(codex_home, "sessions"))    # → CodexSource.available()
        os.environ["CLAUDE_CONFIG_DIR"] = claude_home
        os.environ["CODEX_HOME"] = codex_home
        os.environ.pop("CC_COPILOT_AGENTS", None)
        CX._HEAD_CACHE.clear()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_env_filter_restricts_agents(self):
        os.environ["CC_COPILOT_AGENTS"] = "claude"
        self.assertEqual([s.name for s in SRC.enabled_sources()], ["claude"])

    def test_explicit_agents_arg_overrides_env(self):
        os.environ["CC_COPILOT_AGENTS"] = "claude"
        self.assertEqual([s.name for s in SRC.enabled_sources(agents=["codex"])], ["codex"])

    def test_both_available_by_default(self):
        self.assertEqual({s.name for s in SRC.enabled_sources()}, {"claude", "codex"})

    def test_unavailable_source_is_skipped(self):
        # remove Codex's sessions dir so it reports unavailable; Claude stays
        import shutil
        shutil.rmtree(os.path.join(os.environ["CODEX_HOME"], "sessions"))
        names = [s.name for s in SRC.enabled_sources()]
        self.assertIn("claude", names)
        self.assertNotIn("codex", names)


class TestUnionDiscovery(unittest.TestCase):
    def test_list_sessions_unions_and_sorts_newest_first(self):
        import tempfile
        # a Codex home with one session for /proj/X
        home = tempfile.mkdtemp(prefix="cccodex-union-")
        sdir = os.path.join(home, "sessions", "2026", "06", "07")
        os.makedirs(sdir)
        UX.write_rollout([UX.session_meta(cwd="/proj/X",
                                          sid="019ea000-0000-7000-8000-00000000aaaa"),
                          UX.umsg("codex work", ago=5)], dir=sdir,
                         name="rollout-2026-06-07T10-00-00-019ea000-0000-7000-8000-00000000aaaa.jsonl")
        old_home = os.environ.get("CODEX_HOME")
        old_agents = os.environ.get("CC_COPILOT_AGENTS")
        os.environ["CODEX_HOME"] = home
        os.environ.pop("CC_COPILOT_AGENTS", None)
        CX._HEAD_CACHE.clear()
        try:
            refs = SRC.list_sessions("/proj/X")
        finally:
            if old_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old_home
            if old_agents is not None:
                os.environ["CC_COPILOT_AGENTS"] = old_agents
        # Claude has no canonical project for /proj/X here, so only the Codex
        # session is found — but it comes through the union dispatcher tagged.
        self.assertTrue(any(r.agent == "codex" for r in refs))


class TestCrossAgentCandidates(unittest.TestCase):
    def test_claude_anchor_finds_codex_sibling_by_cwd(self):
        import tempfile
        from cccopilot import scope as SC
        cwd = "/proj/Y"
        # Claude anchor in a plain dir (not the canonical projects dir)
        cdir = tempfile.mkdtemp(prefix="ccclaude-anchor-")
        anchor = UC.write([UC.user("claude work", 30, sessionId="claudeA", cwd=cwd),
                           UC.asst("ok", 5)], dir=cdir)
        # a Codex session for the SAME project cwd, in a temp Codex home
        home = tempfile.mkdtemp(prefix="cccodex-sib-")
        sdir = os.path.join(home, "sessions", "2026", "06", "07")
        os.makedirs(sdir)
        UX.write_rollout([UX.session_meta(cwd=cwd,
                                          sid="019ea000-0000-7000-8000-00000000dddd"),
                          UX.umsg("codex work", ago=10)], dir=sdir,
                         name="rollout-2026-06-07T10-00-00-019ea000-0000-7000-8000-00000000dddd.jsonl")
        old_home = os.environ.get("CODEX_HOME")
        old_agents = os.environ.get("CC_COPILOT_AGENTS")
        os.environ["CODEX_HOME"] = home
        os.environ.pop("CC_COPILOT_AGENTS", None)
        CX._HEAD_CACHE.clear()
        try:
            refs = SC._candidate_refs(anchor)
        finally:
            if old_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old_home
            if old_agents is not None:
                os.environ["CC_COPILOT_AGENTS"] = old_agents
        agents = {r.agent for r in refs}
        self.assertEqual(agents, {"claude", "codex"})


class TestBaseContract(unittest.TestCase):
    def test_abstract_methods_raise(self):
        s = AgentSource()
        self.assertRaises(NotImplementedError, s.available)
        self.assertRaises(NotImplementedError, s.owns, "/x")
        self.assertRaises(NotImplementedError, s.parse, "/x")


if __name__ == "__main__":
    unittest.main()
