"""Invariant A: secret-shaped content is scrubbed from the model-bound copy."""

import unittest

from cccopilot import redact
from cccopilot import narrate


class TestRedact(unittest.TestCase):
    def _scrubbed(self, text):
        out = redact.redact(text)
        return out

    def test_known_token_prefixes_are_redacted(self):
        cases = [
            "AKIA1234567890ABCDEF",                       # AWS access key id
            "sk-abcdEFGH1234567890abcdEFGH",              # OpenAI-style key
            "sk-ant-api03-abcdef1234567890ABCDEF",        # Anthropic key
            "ghp_" + "A" * 36,                            # GitHub PAT
            "github_pat_" + "a" * 30,
            "xoxb-123456789012-abcdefghijkl",             # Slack bot token
            "AIza" + "b" * 35,                            # Google API key
            "sk_live_" + "0" * 24,                        # Stripe live key
        ]
        for secret in cases:
            out = self._scrubbed(f"the key is {secret} ok")
            self.assertNotIn(secret, out, f"{secret!r} leaked")
            self.assertIn("redacted", out)

    def test_private_key_block_redacted(self):
        pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
               "MIIEowIBAAKCAQEArandombase64stuffhere==\n"
               "-----END RSA PRIVATE KEY-----")
        out = self._scrubbed("here:\n" + pem + "\nend")
        self.assertNotIn("MIIEowIBAAKCAQEA", out)
        self.assertIn("[redacted:private-key]", out)

    def test_env_assignment_keeps_name_scrubs_value(self):
        out = self._scrubbed('OPENAI_API_KEY="sk-supersecretvalue123456"')
        self.assertIn("OPENAI_API_KEY", out)          # name kept for context
        self.assertNotIn("supersecretvalue", out)     # value scrubbed
        out2 = self._scrubbed("DATABASE_PASSWORD=hunter2hunter2")
        self.assertIn("DATABASE_PASSWORD", out2)
        self.assertNotIn("hunter2hunter2", out2)

    def test_quoted_and_json_secret_assignments(self):
        # JSON-style and quoted values (incl. spaces) must be fully scrubbed.
        for case, secret in [
            ('"DATABASE_PASSWORD": "hunter2"', "hunter2"),
            ('DATABASE_PASSWORD="correct horse battery"', "correct horse battery"),
            ("client_secret: 'abc def ghi jkl'", "abc def ghi jkl"),
            ('settings["DATABASE_PASSWORD"] = "hunter2hunter2"', "hunter2hunter2"),
            ("os.environ['API_TOKEN'] = 'tok_abcdefgh1234'", "tok_abcdefgh1234"),
            ('{"credentials": {"api_key": "hunter2nested"}}', "hunter2nested"),
        ]:
            out = self._scrubbed(case)
            self.assertNotIn(secret, out, f"{case!r} leaked {secret!r}")
            self.assertIn("[redacted]", out)

    def test_authorization_header_redacted(self):
        out = self._scrubbed("Authorization: Bearer eyAbCdEf12345.payload.sig")
        self.assertNotIn("eyAbCdEf12345", out)
        self.assertIn("Authorization", out)

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOi.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4"
        out = self._scrubbed("token " + jwt)
        self.assertNotIn(jwt, out)

    # ---- must NOT over-redact real evidence ------------------------------
    def test_git_sha_survives(self):
        sha = "58c8358a1b2c3d4e5f60718293a4b5c6d7e8f901"   # 40-hex
        out = self._scrubbed(f"commit {sha} fixed it")
        self.assertIn(sha, out)

    def test_citations_and_prose_survive(self):
        text = ("The agent ran `pytest` at [L142] and edited `app/main.py`. "
                "All 12 tests pass. See README.md for OPENAI_API_KEY usage.")
        out = self._scrubbed(text)
        self.assertEqual(out, text)        # nothing token-shaped → untouched

    def test_unquoted_secret_value_with_spaces_fully_redacted(self):
        # YAML/log plain scalars with spaces must be scrubbed in full.
        for case, leak in [
            ("PASSWORD: correct horse battery", "battery"),
            ("PASSPHRASE=correct horse", "horse"),
        ]:
            out = self._scrubbed(case)
            self.assertNotIn(leak, out, f"{case!r} leaked {leak!r}")

    def test_secret_value_does_not_eat_trailing_citation(self):
        # A [L<n>] citation after a secret assignment on the same line survives.
        out = self._scrubbed("token: foosecretval [src/x.py:L12]")
        self.assertIn("[src/x.py:L12]", out)
        self.assertNotIn("foosecretval", out)

    def test_project_citations_with_secret_words_survive(self):
        # Redaction must not corrupt a [path:L<n>] citation whose path contains a
        # secret-ish word — the model is required to keep these citations.
        for cite in ("[src/token.py:L123]", "[config/passwords.yaml:L42]",
                     "see [auth/secret_store.py:L7] for details"):
            self.assertEqual(self._scrubbed(cite), cite)

    def test_idempotent(self):
        once = self._scrubbed("key sk-abcdEFGH1234567890abcdEFGH here")
        twice = self._scrubbed(once)
        self.assertEqual(once, twice)

    def test_empty_and_non_str_safe(self):
        self.assertEqual(redact.redact(""), "")
        self.assertIsNone(redact.redact(None))

    # ---- wired into the single narration chokepoint ----------------------
    def test_prompt_chokepoint_scrubs_evidence(self):
        brief = "tool_result [L7]: AWS_SECRET_ACCESS_KEY=abcd1234EFGH5678ijkl"
        prompt = narrate._prompt(brief, "recap it")
        self.assertNotIn("abcd1234EFGH5678ijkl", prompt)
        self.assertIn("AWS_SECRET_ACCESS_KEY", prompt)   # label kept, value gone


if __name__ == "__main__":
    unittest.main()
