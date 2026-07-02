"""Content-level secret redaction for the model-bound evidence copy.

cc-copilot keeps a hard invariant independent of its read-only default:
**never leak a project's secrets into LLM context**. :mod:`cccopilot.scope` already refuses to
read secret-*named* files (``.env``, ``*.pem`` …) by basename — but that does
nothing about a secret that lives *inside* otherwise-legitimate evidence: an
inline ``AKIA…`` key in a tracked ``.py``, a ``tool_result`` echoing
``cat .env``, or a token sitting in ``AGENTS.md`` / ``CLAUDE.md`` (which the
evidence engine deliberately ingests).

This pass scrubs that content *on the copy that goes to the model* — applied at
the single narration chokepoint (:func:`cccopilot.narrate._prompt`). It never
touches the on-disk transcript, the cited ``[L<n>]`` line mapping, or what the
cockpit shows the human in their own terminal: the supervisor still sees the
real value locally; only the third-party model gets the scrubbed copy.

Design rules:
- **Recall over precision.** Over-redacting the model copy is harmless; leaking
  is not. False positives degrade an answer at worst; a leak is a breach.
- **Stdlib only.** Plain ``re`` — no entropy classifiers, no dependencies.
- **Never redact real evidence shapes.** Bare hex (git SHAs), line citations
  (``[L142]``), and ordinary prose must survive untouched, so the patterns are
  anchored on known token *prefixes*, key/secret *assignments*, and auth
  *headers* — never on length/entropy alone.
- **Idempotent.** The placeholder never matches a pattern, so re-running is safe.
"""

from __future__ import annotations

import re

_REDACTED = "[redacted]"

# Ordered (compiled regex, replacement) pairs. Order matters: multiline key
# blocks and key=value/auth patterns (which preserve a label) run before the
# bare-token sweeps so the label survives and the value is scrubbed.
_PATTERNS = [
    # PEM / OpenSSH private-key blocks (any key type), header to footer.
    (re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.S), "[redacted:private-key]"),

    # Authorization / proxy-authorization headers: keep the header name, scrub
    # the credential. Stops before a `[` so a trailing `[L<n>]` citation on the
    # same line survives; a separator is required so prose merely mentioning
    # "authorization" is not eaten.
    (re.compile(r"(?i)\b((?:proxy-)?authorization)\s*[:=]\s*[^\n\[]+"),
     r"\1: " + _REDACTED),
    # Bearer <token> (space-separated; common in curl / logged auth headers).
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer " + _REDACTED),

    # KEY=VALUE / KEY: VALUE / "KEY": "VALUE" where the key NAME looks secret.
    # Keeps the name (so the model still knows a credential exists) and scrubs
    # the value, which may be quoted and contain spaces (`PASSWORD="correct
    # horse"`) or JSON-quoted (`"DATABASE_PASSWORD": "hunter2"`). NOT line-
    # anchored — secrets appear mid-line in tool output (`cat .env`) and after
    # `[L<n>]` citation prefixes. A separator is required so doc prose
    # ("OPENAI_API_KEY usage", no `:`/`=`) is not caught.
    (re.compile(
        r"(?i)([\"']?\b[\w.\-]*(?:secret|token|password|passwd|api[_-]?key|"
        r"access[_-]?key|private[_-]?key|client[_-]?secret|credential|"
        r"passphrase|auth[_-]?token)[\w.\-]*\b[\"']?\s*\]?\s*[:=]\s*)"
        # value: a quoted string (may contain spaces), or an unquoted plain
        # scalar that MAY contain spaces (YAML/log `PASSWORD: correct horse
        # battery`) but stops at a structural delimiter (`,;}` newline) or a `[`
        # — the latter so a trailing `[path:L<n>]` citation on the same line is
        # never consumed. It must NOT start with a `{`/`[` object/array (so a
        # nested secret like `{"credentials": {"api_key": "x"}}` falls through to
        # the inner key) nor be a `[path:L<n>]` citation line-ref.
        r"(?:\"[^\"\n]{2,}\"|'[^'\n]{2,}'"
        r"|(?!\s*[{\[])(?!L\d+\]?(?:[\s)\];,]|$))[^,;\[\]}\n]{4,})"),
     r"\1" + _REDACTED),

    # Known high-confidence token shapes (prefix-anchored — won't hit git SHAs).
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}\b"),
     "[redacted:aws-key]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"), "[redacted:token]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[redacted:token]"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}\b"), "[redacted:token]"),
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{12,}\b"), "[redacted:token]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[redacted:token]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[redacted:token]"),
    (re.compile(r"\bya29\.[0-9A-Za-z_\-]{10,}\b"), "[redacted:token]"),
    (re.compile(r"\bSG\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{16,}\b"), "[redacted:token]"),
    # JWTs (three base64url segments) — common in cached auth/tool output.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\b"),
     "[redacted:jwt]"),
    # Credentials embedded in a URL / connection string: scheme://user:PASSWORD@host
    # or the password-only form scheme://:PASSWORD@host — keep scheme/user + host,
    # scrub the password. (`[^\s:/@]*` so the username may be empty.)
    (re.compile(r"(?i)([a-z][a-z0-9+.\-]*://[^\s:/@]*:)[^\s@/]+(@)"),
     r"\1" + _REDACTED + r"\2"),
]


def redact(text: str) -> str:
    """Return ``text`` with secret-shaped content replaced by placeholders.

    Safe on any input (returns non-strings unchanged), idempotent, and tuned to
    leave ordinary evidence — prose, file paths, git SHAs, ``[L<n>]`` citations
    — intact while scrubbing credentials. This is a security control, not a
    parser: when in doubt it over-redacts.
    """
    if not text:
        return text
    for rx, repl in _PATTERNS:
        text = rx.sub(repl, text)
    return text
