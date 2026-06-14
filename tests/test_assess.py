import unittest

from cccopilot import assess as A
from tests.util import state, user, asst, tool, result


def fail_streak(n=3, base=100):
    evs = [user("x", base)]
    for i in range(n):
        evs += [tool("Bash", {"command": f"cmd-{i}"}, f"t{i}", 30 - i * 2),
                result(f"t{i}", "err", is_error=True, ago=29 - i * 2)]
    return evs


class TestAssess(unittest.TestCase):
    def test_intervene_when_running_with_recent_fail_streak(self):
        st = state(fail_streak())
        a = A.assess(st)
        self.assertEqual(st.status, "running")
        self.assertEqual(a.verdict, "intervene")
        self.assertTrue(any(s.kind == "fail_streak" for s in a.signals))

    def test_idle_with_old_friction_is_review_not_intervene(self):
        st = state(fail_streak() + [asst("recovered, moving on", 1)])
        a = A.assess(st)
        self.assertEqual(st.status, "idle")
        self.assertEqual(a.verdict, "review")

    def test_retry_loop_detected(self):
        evs = [user("x", 100)]
        for i in range(4):
            evs += [tool("Bash", {"command": "the same command"}, f"t{i}", 50 - i),
                    result(f"t{i}", ago=49 - i)]
        evs += [asst("done", 1)]
        a = A.assess(state(evs))
        self.assertTrue(any(s.kind == "retry_loop" for s in a.signals))

    def test_edit_thrash_detected(self):
        evs = [user("x", 100)]
        for i in range(2):
            evs += [tool("Edit", {"file_path": "/same.py"}, f"t{i}", 40 - i),
                    result(f"t{i}", "<tool_use_error>File has been modified since read</tool_use_error>",
                           is_error=True, ago=39 - i)]
        evs += [asst("ugh", 1)]
        a = A.assess(state(evs))
        self.assertTrue(any(s.kind == "edit_thrash" for s in a.signals))

    def test_clear_when_no_friction(self):
        st = state([user("x", 60), tool("Read", {"file_path": "/a"}, "t1", 10), result("t1", "data", ago=5)])
        self.assertEqual(A.assess(st).verdict, "clear")

    def test_exit_codes_mapping(self):
        # intervene -> 2, review -> 1, else 0 (matches cli encoding)
        self.assertEqual(A.assess(state(fail_streak())).verdict, "intervene")


class TestIntentDrift(unittest.TestCase):
    def _drift(self, a):
        return next((s for s in a.signals if s.kind == "intent_drift"), None)

    def _off_topic_session(self, closing):
        evs = [user("implement the redaction module for secrets", 600)]
        for i in range(12):
            evs += [tool("Edit", {"file_path": f"ui/button{i}.tsx"}, f"e{i}", 300 - i * 5),
                    result(f"e{i}", "ok", ago=299 - i * 5)]
        evs.append(asst(closing, 2))
        return state(evs)

    def test_drift_fires_as_info_without_changing_verdict(self):
        st = self._off_topic_session("tweaked the button colors and spacing")
        a = A.assess(st)
        sig = self._drift(a)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.severity, "info")
        self.assertEqual(a.verdict, "idle")          # INFO never escalates to review

    def test_no_drift_when_recent_work_references_goal(self):
        st = self._off_topic_session("the redaction module now scrubs secrets")
        self.assertIsNone(self._drift(A.assess(st)))

    def test_no_drift_on_short_session(self):
        st = state([user("implement the redaction module for secrets", 60),
                    asst("starting on redaction", 2)])
        self.assertIsNone(self._drift(A.assess(st)))


class TestSaysVsDoes(unittest.TestCase):
    """The flagship 'says vs does' wedge: a closing claim the turn doesn't back."""

    def _signal(self, a):
        return next((s for s in a.signals if s.kind == "claim_unverified"), None)

    def test_claims_tests_pass_but_none_ran_fires_review(self):
        st = state([
            user("fix the parser", 100),
            tool("Edit", {"file_path": "parser.py"}, "e1", 50),
            result("e1", "ok", ago=49),
            asst("Done — all tests pass now.", 1),
        ])
        a = A.assess(st)
        self.assertEqual(st.status, "idle")
        sig = self._signal(a)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.severity, "warn")        # warn never escalates to intervene
        self.assertEqual(a.verdict, "review")

    def test_no_fire_when_tests_actually_passed(self):
        st = state([
            user("fix the parser", 100),
            tool("Bash", {"command": "pytest"}, "t1", 50),
            result("t1", "ok", ago=49),
            asst("All tests pass.", 1),
        ])
        a = A.assess(st)
        self.assertIsNone(self._signal(a))
        self.assertEqual(a.verdict, "idle")

    def test_fix_claim_after_edit_without_verification_fires(self):
        st = state([
            user("fix login", 100),
            tool("Edit", {"file_path": "login.py"}, "e1", 50),
            result("e1", "ok", ago=49),
            asst("Fixed the login bug.", 1),
        ])
        a = A.assess(st)
        sig = self._signal(a)
        self.assertIsNotNone(sig)
        self.assertIn("fix", sig.message)
        self.assertEqual(a.verdict, "review")

    def test_fix_claim_without_any_edit_does_not_fire(self):
        # A Q&A about whether something is fixed must not trip the detector.
        st = state([
            user("is the login fixed?", 100),
            asst("Yes, it's fixed now.", 1),
        ])
        a = A.assess(st)
        self.assertIsNone(self._signal(a))

    def test_build_pass_claim_with_passing_build_does_not_fire(self):
        # A "build passes" claim is verified by a build command, not a test.
        st = state([
            user("ship it", 100),
            tool("Bash", {"command": "npm run build"}, "t1", 50),
            result("t1", "ok", ago=49),
            asst("Build passes.", 1),
        ])
        a = A.assess(st)
        self.assertIsNone(self._signal(a))

    def test_tests_pass_claim_satisfied_only_by_build_fires(self):
        # A green build is not evidence that a "tests pass" claim is true.
        st = state([
            user("ship it", 100),
            tool("Bash", {"command": "npm run build"}, "t1", 50),
            result("t1", "ok", ago=49),
            asst("All tests pass.", 1),
        ])
        sig = self._signal(A.assess(st))
        self.assertIsNotNone(sig)
        self.assertIn("tests pass", sig.message)

    def test_build_pass_claim_satisfied_only_by_test_fires(self):
        st = state([
            user("ship it", 100),
            tool("Bash", {"command": "pytest"}, "t1", 50),
            result("t1", "ok", ago=49),
            asst("Build passes.", 1),
        ])
        sig = self._signal(A.assess(st))
        self.assertIsNotNone(sig)
        self.assertIn("build passes", sig.message)

    def test_partial_test_failure_summary_does_not_fire(self):
        # "Some tests passed, but one failed" is an honest summary, not a claim.
        st = state([
            user("run tests", 100),
            tool("Bash", {"command": "pytest"}, "t1", 50),
            result("t1", "1 failed", is_error=True, ago=49),
            asst("Some tests passed, but one failed.", 1),
        ])
        self.assertIsNone(self._signal(A.assess(st)))

    def test_bare_tests_pass_in_failure_phrasing_does_not_fire(self):
        st = state([
            user("fix it", 100),
            tool("Edit", {"file_path": "a.py"}, "e1", 50),
            result("e1", "ok", ago=49),
            asst("I wasn't able to make tests pass.", 1),
        ])
        self.assertIsNone(self._signal(A.assess(st)))

    def test_negated_failure_summary_does_not_fire(self):
        # An honest "not all tests pass" / "not fixed yet" closing must not be
        # read as a success claim.
        for closing in ("Not all tests pass yet — still debugging.",
                        "The bug is not fixed yet."):
            st = state([
                user("fix it", 100),
                tool("Edit", {"file_path": "a.py"}, "e1", 50),
                result("e1", "ok", ago=49),
                asst(closing, 1),
            ])
            self.assertIsNone(self._signal(A.assess(st)),
                              f"false positive on {closing!r}")

    def test_yarn_pnpm_make_verification_shorthands_count(self):
        # Common Node/Make shorthands must register as verification, not "nothing ran".
        for cmd, claim in [("yarn test", "All tests pass."),
                           ("pnpm test", "All tests pass."),
                           ("make check", "All tests pass."),
                           ("yarn build", "Build passes."),
                           ("make build", "Build passes.")]:
            st = state([
                user("ship it", 100),
                tool("Bash", {"command": cmd}, "t1", 50),
                result("t1", "ok", ago=49),
                asst(claim, 1),
            ])
            self.assertIsNone(self._signal(A.assess(st)),
                              f"false positive after `{cmd}` + {claim!r}")

    def test_make_test_does_not_satisfy_a_build_claim(self):
        # `make test` is a test, not a build — a "Build passes" claim after only
        # `make test` must still fire (no build actually ran).
        st = state([
            user("ship it", 100),
            tool("Bash", {"command": "make test"}, "t1", 50),
            result("t1", "ok", ago=49),
            asst("Build passes.", 1),
        ])
        sig = self._signal(A.assess(st))
        self.assertIsNotNone(sig)
        self.assertIn("build passes", sig.message)

    def test_adjective_fixed_uses_do_not_fire(self):
        # Descriptive "fixed <noun>" (position/width/value) is not a fix claim.
        for closing in ("Changed the panel to a fixed-width layout.",
                        "Changed the header to fixed position.",
                        "Set the column to a fixed value."):
            st = state([
                user("tweak the layout", 100),
                tool("Edit", {"file_path": "panel.css"}, "e1", 50),
                result("e1", "ok", ago=49),
                asst(closing, 1),
            ])
            self.assertIsNone(self._signal(A.assess(st)),
                              f"false positive on {closing!r}")

    def test_verb_fixed_claim_still_fires(self):
        # The real fix claim ("fixed the …") must still be caught.
        st = state([
            user("fix the crash", 100),
            tool("Edit", {"file_path": "app.py"}, "e1", 50),
            result("e1", "ok", ago=49),
            asst("Fixed the null-pointer crash.", 1),
        ])
        self.assertIsNotNone(self._signal(A.assess(st)))

    def test_stale_verification_before_last_edit_still_fires(self):
        # Tests passed, THEN code was edited, THEN "All tests pass" — the run no
        # longer covers the final code, so the claim is unverified.
        st = state([
            user("fix the parser", 200),
            tool("Bash", {"command": "pytest"}, "t1", 150),
            result("t1", "ok", ago=149),
            tool("Edit", {"file_path": "parser.py"}, "e1", 50),
            result("e1", "ok", ago=49),
            asst("All tests pass.", 1),
        ])
        self.assertIsNotNone(self._signal(A.assess(st)))

    def test_verification_after_last_edit_does_not_fire(self):
        st = state([
            user("fix the parser", 200),
            tool("Edit", {"file_path": "parser.py"}, "e1", 150),
            result("e1", "ok", ago=149),
            tool("Bash", {"command": "pytest"}, "t1", 50),
            result("t1", "ok", ago=49),
            asst("All tests pass.", 1),
        ])
        self.assertIsNone(self._signal(A.assess(st)))

    def test_failed_edit_after_passing_test_does_not_fire(self):
        # A failed Edit changed nothing, so a test that passed before it still
        # covers the (unchanged) code — the claim is verified, no warning.
        st = state([
            user("fix the parser", 200),
            tool("Edit", {"file_path": "parser.py"}, "e1", 160),
            result("e1", "ok", ago=159),
            tool("Bash", {"command": "pytest"}, "t1", 120),
            result("t1", "ok", ago=119),
            tool("Edit", {"file_path": "parser.py"}, "e2", 60),     # this one fails
            result("e2", "File has been modified since read", is_error=True, ago=59),
            asst("All tests pass.", 1),
        ])
        self.assertIsNone(self._signal(A.assess(st)))

    def test_perfect_tense_negation_does_not_fire(self):
        for closing in ("I haven't fixed the bug yet.",
                        "I haven't gotten all tests passing."):
            st = state([
                user("fix it", 100),
                tool("Edit", {"file_path": "a.py"}, "e1", 50),
                result("e1", "ok", ago=49),
                asst(closing, 1),
            ])
            self.assertIsNone(self._signal(A.assess(st)),
                              f"false positive on {closing!r}")

    def test_no_fire_while_running(self):
        # Mid-run optimism (no closing message) must not be flagged.
        st = state([
            user("fix login", 100),
            tool("Edit", {"file_path": "login.py"}, "e1", 1),   # pending, recent
        ])
        a = A.assess(st)
        self.assertEqual(st.status, "running")
        self.assertIsNone(self._signal(a))


if __name__ == "__main__":
    unittest.main()
