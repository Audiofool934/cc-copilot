"""Cockpit TUI tests — focus behaviour and multilingual (CJK) input.

Skipped unless the optional `textual` extra is installed, so the stdlib-only
unit-test pass stays green. The CI runs this pass a second time with textual
installed (see .github/workflows/ci.yml).
"""

import os
import unittest

try:
    from textual import events, on
    from textual.app import App
    from cccopilot import tui
    HAVE_TEXTUAL = True
except Exception:                                   # pragma: no cover
    HAVE_TEXTUAL = False

from tests.util import write, user, asst, tool, result


@unittest.skipUnless(HAVE_TEXTUAL, "textual extra not installed")
class TestComposerCJK(unittest.IsolatedAsyncioTestCase):
    """The composer must accept multilingual input verbatim and submit it."""

    async def test_cjk_keys_insert_and_submit_intact(self):
        captured = []

        class Harness(App):
            def compose(self):
                yield tui.Composer(id="c")

            @on(tui.Composer.Submitted)
            def _cap(self, m):
                captured.append(m.text)

        sample = "你好，世界 — café Ωμέγα 🚀"
        app = Harness()
        async with app.run_test() as pilot:
            comp = app.query_one("#c", tui.Composer)
            comp.focus()
            await pilot.pause()
            for ch in sample:
                comp.post_message(events.Key(key=ch, character=ch))
            await pilot.pause()
            self.assertEqual(comp.text, sample)
            comp.post_message(events.Key(key="enter", character="\r"))
            await pilot.pause()
        self.assertEqual(captured, [sample])

    async def test_enter_submits_shift_enter_newlines(self):
        captured = []

        class Harness(App):
            def compose(self):
                yield tui.Composer(id="c")

            @on(tui.Composer.Submitted)
            def _cap(self, m):
                captured.append(m.text)

        app = Harness()
        async with app.run_test() as pilot:
            comp = app.query_one("#c", tui.Composer)
            comp.focus()
            await pilot.pause()
            comp.insert("line one")
            comp.post_message(events.Key(key="shift+enter", character=None))
            await pilot.pause()
            comp.insert("line two")
            await pilot.pause()
            self.assertEqual(comp.text, "line one\nline two")
            comp.post_message(events.Key(key="enter", character="\r"))
            await pilot.pause()
            # composer clears after submit
            self.assertEqual(comp.text, "")
        self.assertEqual(captured, ["line one\nline two"])


@unittest.skipUnless(HAVE_TEXTUAL, "textual extra not installed")
class TestCockpitFocus(unittest.IsolatedAsyncioTestCase):
    """Clicking anywhere on the cockpit lands focus on the composer."""

    def _session(self):
        from cccopilot.chat import ChatSession
        from cccopilot import narrate as N
        real = N.available
        N.available = lambda b=None: True
        self.addCleanup(lambda: setattr(N, "available", real))
        p = write([user("do it", 120), asst("working", 60),
                   tool("Bash", {"command": "ls"}, "t1", 30),
                   result("t1", ago=20), asst("done.", 5)])
        sess = ChatSession(p, backend="codex")
        sess.refresh()
        return sess

    async def test_panes_are_not_focusable(self):
        app = tui.Cockpit(self._session(), poll=999, alerts=False)
        async with app.run_test():
            self.assertFalse(app.query_one("#chat").can_focus)
            self.assertFalse(app.query_one("#timeline").can_focus)

    async def test_click_pane_keeps_focus_on_composer(self):
        app = tui.Cockpit(self._session(), poll=999, alerts=False)
        async with app.run_test() as pilot:
            comp = app.query_one("#composer", tui.Composer)
            self.assertTrue(comp.has_focus)        # focused on mount
            await pilot.click("#chat")
            await pilot.pause()
            self.assertTrue(comp.has_focus)        # click on chat → still composer
            await pilot.click("#timeline")
            await pilot.pause()
            self.assertTrue(comp.has_focus)        # click on timeline → still composer
            await pilot.click("#status")
            await pilot.pause()
            self.assertTrue(comp.has_focus)        # click on status bar → still composer


@unittest.skipUnless(HAVE_TEXTUAL, "textual extra not installed")
class TestCockpitHistory(unittest.IsolatedAsyncioTestCase):
    """Switching sessions in the cockpit restores prior dialogue (not wiped)."""

    def setUp(self):
        import tempfile
        from cccopilot import narrate as N, store as ST  # noqa
        self.home = tempfile.mkdtemp(prefix="cctui-")
        self._env = {k: os.environ.get(k) for k in
                     ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY", "CC_COPILOT_CONFIG")}
        os.environ["CC_COPILOT_STATE_DIR"] = self.home
        os.environ["CC_COPILOT_HISTORY"] = "1"
        os.environ["CC_COPILOT_CONFIG"] = os.path.join(self.home, "none.toml")
        self._realavail = N.available
        N.available = lambda b=None: True

    def tearDown(self):
        from cccopilot import narrate as N
        N.available = self._realavail
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _session(self, sid):
        from cccopilot.chat import ChatSession
        p = write([user("task", 100, sessionId=sid), asst("ok", 50), asst("done", 5)])
        s = ChatSession(p, backend="codex", alerts=False)
        s.refresh()
        return s

    async def test_answer_persists_and_rebuild_paints(self):
        from textual.widgets import Markdown
        sess = self._session("sess-A")
        sess.history = [("user", "你好"), ("assistant", "**hi** [L3]")]
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.query_one("#chat")
            # restored dialogue painted on mount: 1 user Static + 1 assistant Markdown
            md = chat.query(Markdown)
            self.assertEqual(len(md), 1)

    async def test_in_flight_answer_records_to_origin_after_switch(self):
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            origin_store = app.session.store          # A's store, captured at "submit"
            origin_st = app.session.st
            b = write([user("task", 100, sessionId="sess-B"), asst("ok", 5)])
            app.session.switch_path(b)                # user switches mid-flight
            app._rebuild_chat()
            await pilot.pause()
            # the answer for A returns AFTER the switch
            app._answer_done("q-for-A", "answer-A [L1]", True, origin_st, origin_store)
            await pilot.pause()
        self.assertEqual(origin_store.load_history(),
                         [("user", "q-for-A"), ("assistant", "answer-A [L1]")])
        self.assertEqual(app.session.store.load_history(), [])   # B uncontaminated
        self.assertEqual(app.session.history, [])                # B in-memory clean

    async def test_slash_autocomplete(self):
        from textual.widgets import OptionList
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            comp = app.query_one("#composer", tui.Composer)
            ol = app.query_one("#slash", OptionList)
            comp.text = "/br"
            app._slash_update()
            self.assertTrue(app._slash_open)
            self.assertTrue(ol.display)
            self.assertEqual([ol.get_option_at_index(i).id for i in range(ol.option_count)],
                             ["/brief"])
            app._slash_complete()                       # Tab completes
            self.assertEqual(comp.text, "/brief")
            self.assertFalse(app._slash_open)
            comp.text = "/mod"; app._slash_update(); app._slash_complete()
            self.assertEqual(comp.text, "/model ")      # arg command keeps a space
            comp.text = "hello"; app._slash_update()
            self.assertFalse(app._slash_open)           # non-slash text hides it

    async def test_forget_deletes_saved_history(self):
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._answer_done("q", "a [L1]", True, app.session.st, app.session.store)
            await pilot.pause()
            self.assertTrue(os.path.isfile(sess.store.turns_path))
            app.action_forget()
            await pilot.pause()
        self.assertFalse(os.path.exists(sess.store.turns_path))
        self.assertEqual(sess.history, [])
        self.assertEqual(sess.store.load_history(), [])

    async def test_rewind_forks_conversation(self):
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            for i in range(3):
                app._answer_done(f"q{i}", f"a{i} [L1]", True,
                                 app.session.st, app.session.store)
                await pilot.pause()
            app._rewind_to(1)                           # fork before message #2
            await pilot.pause()
            comp = app.query_one("#composer", tui.Composer)
            self.assertEqual(comp.text, "q1")           # forked message reloaded
        self.assertEqual(sess.history, [("user", "q0"), ("assistant", "a0 [L1]")])
        self.assertEqual(sess.store.load_history(),
                         [("user", "q0"), ("assistant", "a0 [L1]")])

    async def test_answer_done_records_once(self):
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._answer_done("q-one", "the answer [L2]", True,
                             app.session.st, app.session.store)
            await pilot.pause()
        import json
        with open(sess.store.turns_path, encoding="utf-8") as fh:
            turns = [l for l in fh if json.loads(l).get("kind") == "turn"]
        self.assertEqual(len(turns), 1)
        self.assertEqual(sess.store.load_history(),
                         [("user", "q-one"), ("assistant", "the answer [L2]")])


if __name__ == "__main__":
    unittest.main()
