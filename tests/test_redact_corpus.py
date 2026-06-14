"""Invariant A — measured redaction recall against a leak corpus.

The hardest read-only invariant is "never leak a project secret into the LLM
context". This is the safety net for it: a corpus of real secret shapes in real
contexts (env, JSON, YAML, tool output, command args, URLs, citations), asserting
a hard RECALL floor and NAMING any leaker so a dropped pattern is loud — plus a
false-positive guard that ordinary evidence (git SHAs, `[L<n>]` citations, paths)
survives untouched.
"""

import unittest

from cccopilot import redact

# (secret_substring, context_it_is_embedded_in) — must be scrubbed.
LEAK_CORPUS = [
    # provider token shapes (prefix-anchored)
    ("AKIA1234567890ABCDEF", "AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF"),
    ("ASIA1234567890ABCDEF", "export AWS_ACCESS_KEY_ID=ASIA1234567890ABCDEF"),
    ("sk-abcdEFGH1234567890abcdEFGHij", "openai_key: sk-abcdEFGH1234567890abcdEFGHij"),
    ("sk-proj-abcdEFGH1234567890abcd", "OPENAI_API_KEY=sk-proj-abcdEFGH1234567890abcd"),
    ("sk-ant-api03-aaaaaaaaaaaaaaaaaaaa", "ANTHROPIC_API_KEY=sk-ant-api03-aaaaaaaaaaaaaaaaaaaa"),
    ("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "git remote: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    ("gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    ("github_pat_11ABCDEFG0aaaaaaaaaaaabbbb", "github_pat_11ABCDEFG0aaaaaaaaaaaabbbb"),
    ("xoxb-1111111111-222222222222-abcdEFGHijkl", "slack xoxb-1111111111-222222222222-abcdEFGHijkl"),
    ("xoxp-9999999999-abcdefghijkl", "token=xoxp-9999999999-abcdefghijkl"),
    ("AIzaSyA1234567890abcdefghijklmnopqrstuv", "key=AIzaSyA1234567890abcdefghijklmnopqrstuv"),
    ("ya29.a0AfH6SMC1234567890abcdef", "ya29.a0AfH6SMC1234567890abcdef"),
    ("sk_live_abcdefghijklmnop1234", "stripe: sk_live_abcdefghijklmnop1234"),
    ("rk_test_abcdefghijklmnop1234", "rk_test_abcdefghijklmnop1234"),
    ("SG.abcdefghij1234.klmnopqrstuvwxyz123456", "SENDGRID_API_KEY=SG.abcdefghij1234.klmnopqrstuvwxyz123456"),
    ("eyJhbGciOi.eyJzdWIiOiI.SflKxwRJSMeKKF2", "id_token=eyJhbGciOi.eyJzdWIiOiI.SflKxwRJSMeKKF2"),
    # secret-named KEY=VALUE across contexts (env / JSON / YAML / bracketed / quoted)
    ("hunter2hunter2", "DB_PASSWORD=hunter2hunter2"),
    ("hunter2json", '{"db_password": "hunter2json"}'),
    ("hunter2yaml", "client_secret: hunter2yaml"),
    ("correct horse battery", "PASSPHRASE: correct horse battery"),
    ("nestedsecret9", '{"credentials": {"api_key": "nestedsecret9"}}'),
    ("brktsecret8", 'os.environ["SECRET_TOKEN"] = "brktsecret8"'),
    ("accesskeyval7", "aws_secret_access_key = accesskeyval7extra"),
    ("npmtoken5aaaaaaaaaaaaaaaaaaaa", "//registry.npmjs.org/:_authToken=npmtoken5aaaaaaaaaaaaaaaaaaaa"),
    # auth headers / bearer
    ("bearersecret4abcdef", "Authorization: Bearer bearersecret4abcdef"),
    ("apitoken3value", "x-api-key: apitoken3value"),
    # credentials in URLs / connection strings
    ("postgrespw7", "DATABASE_URL=postgres://app:postgrespw7@db.host:5432/prod"),
    ("urlcredpw6", "git clone https://user:urlcredpw6@github.com/x/y.git"),
    ("mongopw2", "mongodb://admin:mongopw2@cluster0.mongodb.net/db"),
    ("redispw1", "redis://default:redispw1@redis.host:6379"),
    ("pwonly3", "REDIS_URL=redis://:pwonly3@redis.host:6379"),   # password-only userinfo
    # secret with a trailing citation on the same line (must scrub, keep citation)
    ("citedsecret0", "tool_result API_KEY=citedsecret0 [L42]"),
    # private-key block
    ("MIIEpQIBAAKCAQEA", "-----BEGIN RSA PRIVATE KEY-----\nMIIEpQIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"),
]

# Ordinary evidence that must NOT be redacted (false-positive guard).
SURVIVE = [
    "58c8358a1b2c3d4e5f60718293a4b5c6d7e8f901",     # git SHA
    "[src/token.py:L123]",                          # citation, secret-ish path
    "see cccopilot/secret_store.py for details",    # secret-ish filename
    "All 12 tests pass.",                           # prose
    "https://github.com/Audiofool934/cc-copilot",   # URL, no creds
    "postgres://localhost:5432/db",                 # connection string, no creds
    "README.md documents OPENAI_API_KEY usage",     # env-var NAME, no value
    "color: #1a2b3c; width: 100px",                 # hex color
    "id 550e8400-e29b-41d4-a716-446655440000",      # UUID
]

class TestRedactionRecall(unittest.TestCase):
    def test_every_corpus_secret_is_redacted(self):
        # Strict gate: EVERY curated secret must be scrubbed, and any leaker is
        # named so a dropped pattern fails loudly (not silently tolerated).
        leakers = [(s[:28], ctx[:48]) for s, ctx in LEAK_CORPUS
                   if s in redact.redact(ctx)]
        recall = 1 - len(leakers) / len(LEAK_CORPUS)
        self.assertEqual(leakers, [],
                         f"redaction leaked {len(leakers)} (recall {recall:.0%}): {leakers}")

    def test_ordinary_evidence_survives_untouched(self):
        for text in SURVIVE:
            self.assertEqual(redact.redact(text), text, f"over-redacted: {text!r}")


if __name__ == "__main__":
    unittest.main()
