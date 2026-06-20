import unittest
from unittest import mock

from cccopilot import narrate as N
from cccopilot.backends import Backend, BackendError


class CaptureBackend(Backend):
    name = "capture"

    def __init__(self):
        self.prompts = []

    def available(self) -> bool:
        return True

    def complete(self, prompt: str, model: str = None, timeout: int = 180) -> str:
        self.prompts.append(prompt)
        return "ok"


class TestNarratePrompt(unittest.TestCase):
    def test_prompt_renames_brief_headings_for_model_context(self):
        prompt = N._prompt("# 🛰  cc-copilot brief — demo\nbody [L1]", "Task")

        self.assertIn("=== EVIDENCE CONTEXT", prompt)
        self.assertLess(prompt.index("=== TASK"), prompt.index("=== EVIDENCE CONTEXT"))
        self.assertLess(prompt.index("=== EVIDENCE CONTEXT"), prompt.index("=== CURRENT TURN"))
        self.assertIn("# cc-copilot evidence context — demo", prompt)
        self.assertNotIn("=== BRIEF", prompt)
        self.assertNotIn("# 🛰  cc-copilot brief", prompt)

    def test_prompt_keeps_current_turn_after_evidence_for_recency(self):
        prompt = N._prompt("# evidence\nbody [L1]", "Stable task",
                           turn_task="Current question: what changed?")

        self.assertLess(prompt.index("Stable task"),
                        prompt.index("=== EVIDENCE CONTEXT"))
        self.assertLess(prompt.index("body [L1]"),
                        prompt.index("Current question: what changed?"))

    def test_chat_prompt_avoids_brief_identity_language(self):
        backend = CaptureBackend()

        N.chat_brief("# cc-copilot multi-session brief — demo\nbody [sess:L1]",
                     [("user", "what next?"), ("assistant", "Check status [sess:L1]")],
                     "so what should I do now?",
                     backend=backend)

        prompt = backend.prompts[0]
        self.assertIn("# cc-copilot multi-session evidence context — demo", prompt)
        self.assertIn("current evidence context", prompt)
        self.assertLess(prompt.index("body [sess:L1]"),
                        prompt.index("so what should I do now?"))
        self.assertNotIn("current brief", prompt.lower())
        self.assertNotIn("=== BRIEF", prompt)

    def test_chat_prompt_guides_multi_session_answers_to_compare_sessions(self):
        backend = CaptureBackend()

        N.chat_brief("# cc-copilot evidence context\n"
                     "scope: `multi-session`\n"
                     "- evidence session(s): `a1b2c3d4`, `b5c6d7e8`\n",
                     [], "where are we stuck?", backend=backend)

        prompt = backend.prompts[0]
        self.assertIn("Scope guidance", prompt)
        self.assertIn("multiple agent sessions", prompt)
        self.assertIn("Do not flatten them into one event stream", prompt)
        self.assertIn("compare by session label", prompt)

    def test_chat_prompt_guides_project_answers_to_decision_mode(self):
        backend = CaptureBackend()

        N.chat_brief("# cc-copilot evidence context\nscope: `project`\n",
                     [], "what should I do?", backend=backend)

        prompt = backend.prompts[0]
        self.assertIn("project-wide evidence", prompt)
        self.assertIn("cross-session risks", prompt)
        self.assertIn("lead with the decision", prompt)

    def test_next_step_prompt_asks_for_the_next_step_grounded_in_evidence(self):
        backend = CaptureBackend()

        out = N.next_step_brief("# 🛰  cc-copilot brief — demo\nbody [L7]", backend=backend)

        self.assertEqual(out, "ok")
        prompt = backend.prompts[0]
        self.assertIn("do NEXT", prompt)                # the next-step task
        self.assertIn("cited evidence", prompt)
        self.assertIn("=== EVIDENCE CONTEXT", prompt)   # grounding pack present
        self.assertIn("[L7]", prompt)                   # citations preserved
        self.assertNotIn("# 🛰  cc-copilot brief", prompt)   # identity cues stripped

    def test_instruction_is_folded_into_next_step_with_grounding_intact(self):
        backend = CaptureBackend()

        N.next_step_brief("# 🛰  cc-copilot brief — demo\nbody [L7]",
                          backend=backend, instruction="in spanish")

        prompt = backend.prompts[0]
        self.assertIn("in spanish", prompt)             # the steer reached the model
        self.assertIn("instruction for how to answer", prompt)
        self.assertIn("never invent facts", prompt)     # grounding contract restated
        self.assertIn("do NEXT", prompt)                # base task still present
        self.assertLess(prompt.index("do NEXT"), prompt.index("=== EVIDENCE CONTEXT"))
        self.assertLess(prompt.index("body [L7]"), prompt.index("in spanish"))

    def test_instruction_is_folded_into_since_recap(self):
        backend = CaptureBackend()

        N.recap_since("# delta\nchanged [L3]", backend=backend,
                      instruction="just the blocker")

        prompt = backend.prompts[0]
        self.assertIn("just the blocker", prompt)
        self.assertIn("never invent facts", prompt)

    def test_watch_progress_prompt_asks_for_process_not_event_log(self):
        backend = CaptureBackend()

        out = N.watch_progress_brief("# delta\n- Bash still running [L3]",
                                     backend=backend)

        self.assertEqual(out, "ok")
        prompt = backend.prompts[0]
        self.assertIn("WATCH DELTA", prompt)
        self.assertIn("readable process update", prompt)
        self.assertIn("not an event log", prompt)
        self.assertIn("[L3]", prompt)

    def test_watch_digest_prompt_asks_for_periodic_monitoring_digest(self):
        backend = CaptureBackend()

        out = N.watch_digest_brief("# buffer\n- pytest running [L8]",
                                   backend=backend)

        self.assertEqual(out, "ok")
        prompt = backend.prompts[0]
        self.assertIn("WATCH STEP DIGEST BUFFER", prompt)
        self.assertIn("posterior", prompt)
        self.assertIn("monitoring digest", prompt)
        self.assertIn("3-5 concise sentences", prompt)
        self.assertIn("do not produce an event log", prompt.lower())
        self.assertIn("[L8]", prompt)

    def test_watch_flow_prompt_combines_now_and_step_decision(self):
        backend = CaptureBackend()

        out = N.watch_flow_update(
            "# flow\n## previous now\npytest is running [L3]\n"
            "## new watch delta\n- pytest failed [L8]",
            backend=backend)

        self.assertEqual(out, "ok")
        prompt = backend.prompts[0]
        self.assertIn("WATCH FLOW CONTEXT", prompt)
        self.assertIn("previous Now update", prompt)
        self.assertIn("now:", prompt)
        self.assertIn("action: same|new", prompt)
        self.assertIn("[L8]", prompt)

    def test_watch_prompts_accept_instruction_like_now(self):
        backend = CaptureBackend()

        N.watch_progress_brief("# delta\n- Bash still running [L3]",
                               backend=backend, instruction="in Chinese")
        N.watch_digest_brief("# buffer\n- pytest running [L8]",
                             backend=backend, instruction="in Chinese")
        N.watch_flow_update("# flow\n- Bash still running [L3]",
                            backend=backend, instruction="in Chinese")

        self.assertIn("instruction for how to answer", backend.prompts[0])
        self.assertIn("in Chinese", backend.prompts[0])
        self.assertIn("instruction for how to answer", backend.prompts[1])
        self.assertIn("in Chinese", backend.prompts[1])
        self.assertIn("instruction for how to answer", backend.prompts[2])
        self.assertIn("in Chinese", backend.prompts[2])

    def test_loop_prompt_drafts_paste_ready_loop_command(self):
        backend = CaptureBackend()

        out = N.loop_brief("# cc-copilot evidence context\nbody [L9]",
                           backend=backend, instruction="check CI every 5m")

        self.assertEqual(out, "ok")
        prompt = backend.prompts[0]
        self.assertIn("paste-ready `/loop ...`", prompt)
        self.assertIn("self-paced", prompt)
        self.assertIn("fixed interval", prompt)
        self.assertIn("loop engineering", prompt)
        self.assertIn("check CI every 5m", prompt)
        self.assertIn("[L9]", prompt)

    def test_transient_backend_errors_retry_complete(self):
        class FlakyBackend(CaptureBackend):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def complete(self, prompt: str, model: str = None, timeout: int = 180) -> str:
                self.calls += 1
                self.prompts.append(prompt)
                if self.calls == 1:
                    raise BackendError("openai connection error: reset")
                return "ok after retry"

        backend = FlakyBackend()
        with mock.patch("cccopilot.narrate.time.sleep"):
            out = N.run_brief("# evidence\nbody [L1]", "Task", backend=backend)

        self.assertEqual(out, "ok after retry")
        self.assertEqual(backend.calls, 2)

    def test_exhausted_transient_error_reports_attempt_count(self):
        class DownBackend(CaptureBackend):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def complete(self, prompt: str, model: str = None, timeout: int = 180) -> str:
                self.calls += 1
                raise BackendError(
                    "deepseek connection error: [Errno 104] Connection reset by peer")

        backend = DownBackend()
        with mock.patch.dict("os.environ", {"CC_COPILOT_MODEL_RETRIES": "1"}), \
             mock.patch("cccopilot.narrate.time.sleep"):
            with self.assertRaises(BackendError) as ctx:
                N.run_brief("# evidence\nbody [L1]", "Task", backend=backend)

        self.assertEqual(backend.calls, 2)
        self.assertIn("after 2 attempts", str(ctx.exception))

    def test_stream_retries_only_before_first_chunk(self):
        class FlakyStreamBackend(Backend):
            name = "flaky"

            def __init__(self):
                self.calls = 0

            def available(self):
                return True

            def stream(self, prompt: str, model: str = None, timeout: int = 180):
                self.calls += 1
                if self.calls == 1:
                    raise BackendError("openai connection error: reset")
                yield "ok"

        backend = FlakyStreamBackend()
        with mock.patch.dict("os.environ", {"CC_COPILOT_STREAM": "1"}), \
             mock.patch("cccopilot.narrate.time.sleep"):
            h = N.run_brief_stream("# evidence\nbody [L1]", "Task", backend=backend)
            out = "".join(h)

        self.assertEqual(out, "ok")
        self.assertEqual(backend.calls, 2)

    def test_watch_step_decision_prompt_is_machine_parseable(self):
        backend = CaptureBackend()

        out = N.watch_step_decision("# current\n- testing\n# delta\n- pytest failed [L8]",
                                    backend=backend)

        self.assertEqual(out, "ok")
        prompt = backend.prompts[0]
        self.assertIn("CURRENT WATCH STEP", prompt)
        self.assertIn("NEW WATCH DELTA", prompt)
        self.assertIn("action: same|new", prompt)
        self.assertIn("title:", prompt)
        self.assertIn("phase:", prompt)

    def test_empty_instruction_leaves_the_task_unchanged(self):
        self.assertEqual(N._with_instruction("BASE TASK", ""), "BASE TASK")
        self.assertEqual(N._with_instruction("BASE TASK", "   "), "BASE TASK")
        self.assertIn("loud", N._with_instruction("BASE TASK", "be loud"))

    def test_chat_history_uses_budget_instead_of_fixed_turn_count(self):
        backend = CaptureBackend()
        history = []
        for i in range(10):
            history.append(("user", f"question {i}"))
            history.append(("assistant", f"answer {i} [sess:L1]"))

        N.chat_brief("# cc-copilot evidence context\nbody [sess:L1]",
                     history, "continue", backend=backend)

        prompt = backend.prompts[0]
        self.assertIn("question 0", prompt)
        self.assertIn("answer 9", prompt)


class TestNextStepReconciliation(unittest.TestCase):
    """`/now` (`_NEXT_STEP_TASK`) is the sole home of the next-step recommendation;
    `/since` and `--narrate` recap + orient but no longer prescribe the next action."""

    def test_since_recap_cedes_next_step_to_now(self):
        self.assertNotIn("thing to look at next", N._SINCE_RECAP_TASK)
        self.assertIn("do NOT prescribe the next action", N._SINCE_RECAP_TASK)

    def test_narrate_task_cedes_next_step_to_now(self):
        self.assertNotIn("thing to look at next", N._NARRATE_TASK)
        self.assertIn("do NOT prescribe the next action", N._NARRATE_TASK)

    def test_dead_narrate_helpers_removed(self):
        # narrate() / narrate_brief() had zero callers; only the streaming sibling
        # (the --narrate path) survives.
        self.assertFalse(hasattr(N, "narrate"))
        self.assertFalse(hasattr(N, "narrate_brief"))
        self.assertTrue(hasattr(N, "narrate_brief_stream"))


if __name__ == "__main__":
    unittest.main()
