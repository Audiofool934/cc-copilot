import unittest

from cccopilot import narrate as N
from cccopilot.backends import Backend


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
        self.assertIn("# cc-copilot evidence context — demo", prompt)
        self.assertNotIn("=== BRIEF", prompt)
        self.assertNotIn("# 🛰  cc-copilot brief", prompt)

    def test_chat_prompt_avoids_brief_identity_language(self):
        backend = CaptureBackend()

        N.chat_brief("# cc-copilot multi-session brief — demo\nbody [sess:L1]",
                     [("user", "what next?"), ("assistant", "Check status [sess:L1]")],
                     "so what should I do now?",
                     backend=backend)

        prompt = backend.prompts[0]
        self.assertIn("# cc-copilot multi-session evidence context — demo", prompt)
        self.assertIn("current evidence context", prompt)
        self.assertNotIn("current brief", prompt.lower())
        self.assertNotIn("=== BRIEF", prompt)


if __name__ == "__main__":
    unittest.main()
