"""Invariant A — the single redaction chokepoint.

Redaction only protects secrets if 100% of model-bound text flows through
``narrate._prompt`` (where ``redact()`` runs). Two guards: (1) every narrate
entry point scrubs an injected secret before it reaches the backend, and (2) no
module other than narrate calls a backend directly (so a future code path can't
quietly bypass the chokepoint).
"""

import glob
import os
import unittest
from unittest import mock

import cccopilot
from cccopilot import narrate as N
from cccopilot.backends import Backend

SENTINEL = "SENTINELVALUE123"
BRIEF = f"tool_result [L7]: db_password={SENTINEL}"


class _Recorder(Backend):
    """A backend that records the exact prompt it's handed (and answers)."""
    name = "recorder"

    def __init__(self):
        self.prompts = []

    def available(self):
        return True

    def complete(self, prompt, model=None, timeout=180):
        self.prompts.append(prompt)
        return "ok"
    # stream() falls back to the base impl, which calls complete() — also recorded.


def _drain(x):
    """Consume a StreamHandle so the backend actually runs; return it."""
    list(x)
    return x


class TestChokepoint(unittest.TestCase):
    def _assert_scrubbed(self, rec):
        self.assertTrue(rec.prompts, "backend was never called")
        for p in rec.prompts:
            self.assertNotIn(SENTINEL, p)
            self.assertIn("db_password", p)        # label kept, value gone

    def test_every_narrate_entrypoint_redacts(self):
        cases = [
            lambda r: N.run_brief(BRIEF, "recap it", backend=r),
            lambda r: N.ask_brief(BRIEF, "what changed?", backend=r),
            lambda r: N.chat_brief(BRIEF, [], "what changed?", backend=r),
            lambda r: N.recap_since(BRIEF, backend=r),
            lambda r: N.next_step_brief(BRIEF, backend=r),
            lambda r: _drain(N.run_brief_stream(BRIEF, "task", backend=r)),
            lambda r: _drain(N.narrate_brief_stream(BRIEF, backend=r)),
            lambda r: _drain(N.next_step_brief_stream(BRIEF, backend=r)),
            lambda r: _drain(N.ask_brief_stream(BRIEF, "q", backend=r)),
            lambda r: _drain(N.chat_brief_stream(BRIEF, [], "q", backend=r)),
        ]
        for call in cases:
            rec = _Recorder()
            call(rec)
            self._assert_scrubbed(rec)

    def test_public_state_entrypoints_redact(self):
        # The public state-based wrappers (run/ask/chat) call render(state) first;
        # patch render to inject the secret-bearing brief and confirm they too
        # funnel through the redacting chokepoint.
        with mock.patch.object(N, "render", return_value=BRIEF):
            for call in (lambda r: N.run(object(), "recap", backend=r),
                         lambda r: N.ask(object(), "what changed?", backend=r),
                         lambda r: N.chat(object(), [], "what changed?", backend=r)):
                rec = _Recorder()
                call(rec)
                self._assert_scrubbed(rec)

    def test_only_narrate_calls_a_backend(self):
        # Coarse architectural tripwire: direct backend calls must live only in
        # backends.py (the impls) and narrate.py (the chokepoint). A new
        # `.complete(`/`.stream(` anywhere else means model traffic that skips
        # redaction — fail loudly so it's a deliberate, reviewed decision.
        root = os.path.dirname(cccopilot.__file__)
        allowed = {"backends.py", "narrate.py"}     # exact paths under the package root
        offenders = []
        for path in glob.glob(os.path.join(root, "**", "*.py"), recursive=True):
            rel = os.path.relpath(path, root)
            if rel in allowed:                       # NOT basename — a sources/narrate.py is scanned
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            if ".complete(" in src or ".stream(" in src:
                offenders.append(rel)
        self.assertEqual(offenders, [],
                         f"model traffic must go through narrate._prompt; "
                         f"direct backend call(s) found in: {offenders}")


if __name__ == "__main__":
    unittest.main()
