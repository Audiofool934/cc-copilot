"""Away-alert transition logic and notification delivery fallback."""

import types
import unittest

from cccopilot import notify as NO
from cccopilot.state import Diff


def _diff(status_from="idle", status_to="idle", verdict_from="clear",
          verdict_to="clear", new_failures=None):
    return Diff(new_events=1, status_from=status_from, status_to=status_to,
                verdict_from=verdict_from, verdict_to=verdict_to,
                new_failures=new_failures or [], new_changed=[])


def _fail(line=42, tool="Bash"):
    return types.SimpleNamespace(line=line, tool=tool)


class TestAlertForDiff(unittest.TestCase):
    def test_into_intervene_alerts(self):
        msg = NO.alert_for_diff(_diff(verdict_from="clear", verdict_to="intervene",
                                      new_failures=[_fail()]))
        self.assertIsNotNone(msg)
        self.assertIn("intervene", msg)
        self.assertIn("[L42]", msg)

    def test_into_stalled_alerts(self):
        msg = NO.alert_for_diff(_diff(status_from="running", status_to="stalled"))
        self.assertIsNotNone(msg)
        self.assertIn("stalled", msg)

    def test_new_failure_alerts(self):
        msg = NO.alert_for_diff(_diff(new_failures=[_fail(7, "Edit")]))
        self.assertIsNotNone(msg)
        self.assertIn("Edit", msg)

    def test_steady_state_intervene_is_silent(self):
        # already intervene last poll → don't re-alert every tick
        self.assertIsNone(NO.alert_for_diff(
            _diff(verdict_from="intervene", verdict_to="intervene")))

    def test_steady_state_intervene_with_new_failure_is_silent(self):
        # a new failure while ALREADY intervene must not re-alert (leading edge only)
        self.assertIsNone(NO.alert_for_diff(
            _diff(verdict_from="intervene", verdict_to="intervene",
                  new_failures=[_fail(99, "Bash")])))

    def test_clean_is_silent(self):
        self.assertIsNone(NO.alert_for_diff(_diff()))

    def test_recovery_is_silent(self):
        self.assertIsNone(NO.alert_for_diff(
            _diff(verdict_from="intervene", verdict_to="clear")))


class TestDelivery(unittest.TestCase):
    def test_applescript_quote(self):
        self.assertEqual(NO._applescript_quote('a "b" \\ c\nd'), 'a \\"b\\" \\\\ c d')

    def test_desktop_notify_returns_bool_no_crash(self):
        # whatever the platform, it must not raise; returns True (GUI) or False (fallback)
        self.assertIn(NO.desktop_notify("title", "message"), (True, False))


if __name__ == "__main__":
    unittest.main()
