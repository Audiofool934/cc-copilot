"""Away-alerts: tell a human when a watched agent needs them, while they're gone.

Two pieces, both conservative by design (alert fatigue is the failure mode):

- :func:`alert_for_diff` decides whether a state change is worth interrupting a
  human for — only a *transition into* needing attention (a fresh ``intervene``
  verdict, a slide into ``stalled``, or a brand-new failure), never steady-state
  noise.
- :func:`desktop_notify` delivers it: a macOS/Linux desktop notification when
  available, falling back to a terminal bell + stderr line otherwise.

Read-only by default: notifications observe and report; they never touch the agent.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Optional

from .state import Diff


def alert_for_diff(d: Diff) -> Optional[str]:
    """A one-line alert if ``d`` crosses *into* needing attention, else None.

    Conservative: fires on the leading edge only — verdict becoming
    ``intervene``, status sliding into ``stalled``, or a new failure appearing —
    so a session that is *already* flagged doesn't re-alert every poll.
    """
    became_intervene = d.verdict_to == "intervene" and d.verdict_from != "intervene"
    became_stalled = d.status_to == "stalled" and d.status_from != "stalled"
    new_fail = len(d.new_failures)

    if became_intervene:
        tail = d.new_failures[-1] if d.new_failures else None
        why = f" — {tail.tool} failed [L{tail.line}]" if tail else ""
        return f"needs you: off-track / intervene{why}"
    if became_stalled:
        return "stalled — agent stopped mid-action (interrupted or stuck)"
    # A new failure is worth a ping only if the session wasn't already flagged —
    # once it's intervene, the human knows; re-alerting each poll is just noise.
    if new_fail and d.verdict_from != "intervene":
        f = d.new_failures[-1]
        return f"{new_fail} new failure(s) — {f.tool} [L{f.line}]"
    return None


def _applescript_quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def desktop_notify(title: str, message: str) -> bool:
    """Best-effort desktop notification. Returns True if a GUI toast was shown.

    Falls back to a terminal bell + stderr line (always "delivered" to a present
    operator, but returns False so callers can tell it wasn't a desktop toast).
    """
    msg = " ".join((message or "").split())
    if sys.platform == "darwin" and shutil.which("osascript"):
        script = (f'display notification "{_applescript_quote(msg)}" '
                  f'with title "{_applescript_quote(title)}"')
        try:
            subprocess.run(["osascript", "-e", script], check=False,
                           timeout=5, capture_output=True)
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    elif shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", title, msg], check=False,
                           timeout=5, capture_output=True)
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    # fallback: bell + stderr so a present operator still sees it
    try:
        sys.stderr.write(f"\a🔔 {title}: {msg}\n")
        sys.stderr.flush()
    except OSError:
        pass
    return False
