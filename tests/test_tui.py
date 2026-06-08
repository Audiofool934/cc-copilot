"""Cockpit TUI tests — focus behaviour and multilingual (CJK) input.

Skipped unless the optional `textual` extra is installed, so the stdlib-only
unit-test pass stays green. The CI runs this pass a second time with textual
installed (see .github/workflows/ci.yml).
"""

import os
import json
import types
import unittest

try:
    from textual import events, on
    from textual.app import App
    from cccopilot import tui
    HAVE_TEXTUAL = True
except Exception:                                   # pragma: no cover
    HAVE_TEXTUAL = False

from tests.util import write, user, asst, tool, result


def _timeline_text(app):
    """Title + the RichLog activity lines, as one string for content assertions."""
    from textual.widgets import RichLog, Static
    title = str(app.query_one("#timeline-title", Static).content)
    rl = app.query_one("#timeline-log", RichLog)
    return title + "\n" + "\n".join(s.text for s in rl.lines)


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
            self.assertFalse(app.query_one("#status-header").can_focus)
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
class TestPickerKeyboard(unittest.IsolatedAsyncioTestCase):
    """The shared picker must be usable without a mouse."""

    async def test_arrow_enter_selects_row(self):
        from textual.widgets import OptionList, Static

        chosen = []

        class Harness(App):
            def compose(self):
                yield Static("root")

        app = Harness()
        async with app.run_test() as pilot:
            picker = tui.Picker("pick", [("Alpha", "a"), ("Beta", "b"), ("Gamma", "g")])
            await app.push_screen(picker, chosen.append)
            await pilot.pause()
            ol = picker.query_one("#picker-list", OptionList)
            self.assertEqual(ol.highlighted, 0)

            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(ol.highlighted, 1)
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(chosen, ["b"])

    async def test_filter_resets_highlight_and_enter_selects(self):
        from textual.widgets import OptionList, Static

        chosen = []

        class Harness(App):
            def compose(self):
                yield Static("root")

        app = Harness()
        async with app.run_test() as pilot:
            picker = tui.Picker("pick", [("Alpha", "a"), ("Beta", "b"), ("Gamma", "g")])
            await app.push_screen(picker, chosen.append)
            await pilot.pause()

            await pilot.press("g", "a")
            await pilot.pause()
            ol = picker.query_one("#picker-list", OptionList)
            self.assertEqual(ol.option_count, 1)
            self.assertEqual(ol.highlighted, 0)
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(chosen, ["g"])

    async def test_multi_picker_space_toggles_and_enter_selects(self):
        from textual.widgets import OptionList, Static

        chosen = []

        class Harness(App):
            def compose(self):
                yield Static("root")

        app = Harness()
        async with app.run_test() as pilot:
            picker = tui.MultiPicker("pick many", [
                ("Alpha", "a"), ("Beta", "b"), ("Gamma", "g"),
            ])
            await app.push_screen(picker, chosen.append)
            await pilot.pause()
            ol = picker.query_one("#picker-list", OptionList)
            self.assertEqual(ol.highlighted, 0)
            self.assertIn("[ ] Alpha", str(ol.get_option_at_index(0).prompt))
            # the picker explains itself: a key hint and a live selected count
            hint = str(picker.query_one("#picker-hint", Static).content)
            self.assertIn("Space", hint)
            self.assertIn("Enter", hint)
            self.assertIn("(0 selected)", str(picker.query_one("#picker-title", Static).content))

            await pilot.press("down", "space")          # toggle Beta on
            await pilot.pause()
            self.assertIn("[x] Beta", str(ol.get_option_at_index(1).prompt))
            self.assertIn("(1 selected)", str(picker.query_one("#picker-title", Static).content))

            await pilot.press("enter")                  # confirm
            await pilot.pause()

        self.assertEqual(chosen, [["b"]])

    async def test_picker_highlight_has_visible_selection_band(self):
        # the cursor row must use a distinct, theme-derived band (not the near-
        # invisible default) and stay visible when the list isn't focused
        css = tui.Cockpit.CSS
        self.assertIn("option-list--option-highlighted", css)
        self.assertIn("$secondary", css)

    async def test_session_picker_label_includes_title(self):
        ref = types.SimpleNamespace(
            title="test-session-A", session_id="abcdef123456", size=4096,
            path="/tmp/abcdef123456.jsonl")
        label = tui._session_picker_label(ref, current_path=ref.path)
        self.assertIn("test-session-A", label)
        self.assertIn("abcdef12", label)
        self.assertIn("(current)", label)

    async def test_agent_indicator_helpers(self):
        codex = types.SimpleNamespace(
            path="/x/rollout-2026-06-07T10-00-00-019ea15e7785aaaa.jsonl", st=None)
        claude = types.SimpleNamespace(path="/some/plain/abc.jsonl", st=None)
        self.assertEqual(tui._agent_of(codex), "codex")
        self.assertEqual(tui._agent_of(claude), "claude")  # default for unowned paths
        self.assertTrue(tui._sub_title(codex).startswith("codex "))
        mix = [(types.SimpleNamespace(agent="codex"), None, None),
               (types.SimpleNamespace(agent="claude"), None, None),
               (types.SimpleNamespace(agent="codex"), None, None)]
        label = tui._agent_mix(mix)
        self.assertIn("2 codex", label)
        self.assertIn("1 claude", label)
        # one agent → no mix noise
        self.assertEqual(tui._agent_mix([(types.SimpleNamespace(agent="codex"), None, None)]), "")

    async def test_session_picker_label_shows_agent(self):
        ref = types.SimpleNamespace(
            title="codex work", session_id="019ea15e7785", size=2048,
            path="/tmp/rollout-x.jsonl", agent="codex")
        self.assertIn("codex", tui._session_picker_label(ref))

    async def test_session_selection_matches_codex_path_by_ref(self):
        # Codex filenames aren't bare session ids; selection must match by path
        refs = [types.SimpleNamespace(
            session_id="019ea15e7785",
            path="/tmp/rollout-2026-06-07T10-00-00-019ea15e7785.jsonl")]
        sess = types.SimpleNamespace(
            scope=tui.SC.SESSION, scope_sessions=[],
            path="/tmp/rollout-2026-06-07T10-00-00-019ea15e7785.jsonl")
        self.assertEqual(tui._session_selection_ids(sess, refs), ["019ea15e7785"])

    async def test_busy_indicator_rotates(self):
        self.assertNotEqual(tui._busy_indicator(0), tui._busy_indicator(1))
        self.assertEqual(tui._busy_indicator(0), tui._busy_indicator(len(tui._BUSY_FRAMES)))

    async def test_timeline_delta_line_mentions_text_only_events(self):
        from cccopilot import state as S, transcript as T
        old = S.build(T.parse(write([user("task", 60)])))
        new = S.build(T.parse(write([user("task", 60), asst("done", 1)])))

        line = str(tui._timeline_delta_line(new, S.diff(old, new)))
        self.assertIn("+1 events", line)
        self.assertIn("awaiting-agent", line)
        self.assertIn("idle", line)

    async def test_deprecated_control_shortcuts_are_not_advertised_or_bound(self):
        keys = {binding.key for binding in tui.Cockpit.BINDINGS}
        for key in ("ctrl+s", "ctrl+o", "ctrl+h"):
            self.assertNotIn(key, keys)
        for label in ("Ctrl+S", "Ctrl+O", "Ctrl+H"):
            self.assertNotIn(label, tui._HELP_TEXT)
        self.assertNotIn("/history", [name for name, *_ in tui._SLASH_CMDS])
        self.assertNotIn("/scope", [name for name, *_ in tui._SLASH_CMDS])

    async def test_session_selection_initializes_from_current_evidence(self):
        refs = [
            types.SimpleNamespace(session_id="a", path="/tmp/a.jsonl"),
            types.SimpleNamespace(session_id="b", path="/tmp/b.jsonl"),
        ]
        sess = types.SimpleNamespace(scope=tui.SC.SESSION, scope_sessions=[],
                                     path="/tmp/b.jsonl")
        self.assertEqual(tui._session_selection_ids(sess, refs), ["b"])
        sess.scope = tui.SC.MULTI
        self.assertEqual(tui._session_selection_ids(sess, refs), ["a", "b"])
        sess.scope_sessions = ["a"]
        self.assertEqual(tui._session_selection_ids(sess, refs), ["a"])

    async def test_theme_surface_is_curated(self):
        self.assertEqual(tui.COCKPIT_THEME_NAMES,
                         ("cockpit", "graphite", "signal", "daybreak"))
        self.assertIn("/theme", [name for name, *_ in tui._SLASH_CMDS])
        self.assertEqual(tui._theme_name("Graphite"), "graphite")
        self.assertEqual(tui._theme_name("nope"), "cockpit")
        for name in tui.COCKPIT_THEME_NAMES:
            spec = tui.COCKPIT_THEME_SPECS[name]
            for key in ("primary", "secondary", "accent", "foreground",
                        "background", "surface", "panel", "success",
                        "warning", "error", "muted"):
                self.assertIn(key, spec)


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
        p = write([user("task", 100, sessionId=sid), asst("ok", 50), asst("done", 5)],
                  dir=self.home)
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

    async def test_timeline_rebuild_shows_observed_activity(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            joined = _timeline_text(app)
        self.assertIn("session activity", joined)
        self.assertIn("agent", joined)
        self.assertIn("done", joined)
        self.assertNotIn("window-1", joined)

    async def test_status_header_shows_session_mode(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            header = str(app.query_one("#status-header", Static).content)
        self.assertIn("project", header)
        # single-session header now names the agent it's watching
        self.assertIn("evidence claude session", header)
        self.assertIn("sess-A", header)
        self.assertIn("activity current session", header)

    async def test_timeline_resize_keys_persist_and_clamp(self):
        import tempfile
        from cccopilot import prefs as PREFS
        old = os.environ.get("CC_COPILOT_STATE_DIR")
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="cctl-")
        os.environ.pop("CC_COPILOT_TIMELINE_HEIGHT", None)
        try:
            app = tui.Cockpit(self._session("sess-A"), poll=999, alerts=False)
            async with app.run_test() as pilot:
                await pilot.pause()
                h0 = app._timeline_height
                await pilot.press("shift+up")          # primary (macOS-safe) key
                await pilot.pause()
                self.assertEqual(app._timeline_height, h0 + 1)
                self.assertEqual(PREFS.get_int("timeline_height", -1), h0 + 1)
                await pilot.press("ctrl+up")           # alias still grows
                await pilot.pause()
                self.assertEqual(app._timeline_height, h0 + 2)
                for _ in range(20):
                    await pilot.press("shift+down")    # clamp at the minimum
                await pilot.pause()
                self.assertEqual(app._timeline_height, tui.Cockpit.TIMELINE_MIN)
        finally:
            if old is None:
                os.environ.pop("CC_COPILOT_STATE_DIR", None)
            else:
                os.environ["CC_COPILOT_STATE_DIR"] = old

    async def test_timeline_height_restored_on_launch(self):
        import tempfile
        from cccopilot import prefs as PREFS
        old = os.environ.get("CC_COPILOT_STATE_DIR")
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="cctl2-")
        os.environ.pop("CC_COPILOT_TIMELINE_HEIGHT", None)
        try:
            PREFS.set("timeline_height", 11)
            app = tui.Cockpit(self._session("sess-A"), poll=999, alerts=False)
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertEqual(app._timeline_height, 11)
        finally:
            if old is None:
                os.environ.pop("CC_COPILOT_STATE_DIR", None)
            else:
                os.environ["CC_COPILOT_STATE_DIR"] = old

    async def test_timeline_seeds_history_and_tail_follows(self):
        import json
        import tempfile
        from cccopilot.chat import ChatSession
        d = tempfile.mkdtemp(prefix="ccbig-")
        p = os.path.join(d, "big.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "sessionId": "big", "cwd": "/x",
                                "message": {"role": "user", "content": "go"}}) + "\n")
            for i in range(60):
                f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                        "model": "c", "content": [{"type": "tool_use", "id": f"t{i}",
                        "name": "Bash", "input": {"command": f"cmd-{i}", "description": ""}}]}}) + "\n")
                f.write(json.dumps({"type": "user", "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                     "content": "ok"}]}}) + "\n")
        sess = ChatSession(p, alerts=False, persist=False)
        app = tui.Cockpit(sess, poll=999, alerts=False)
        from textual.widgets import RichLog
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            rl = app.query_one("#timeline-log", RichLog)
            # the ENTIRE session is held (60 events), not a capped handful
            self.assertGreater(len(rl.lines), 55)
            self.assertGreater(rl.max_scroll_y, 0)                  # scrollable
            rl.scroll_to(y=1, animate=False)
            await pilot.pause()
            app._timeline(tui.Text("late event"))                  # tail-follow
            await pilot.pause()
            self.assertLessEqual(rl.scroll_offset.y, 3)            # not yanked to bottom

    async def test_rebuild_keeps_scrolled_up_reader_and_follows_bottom(self):
        """A full rebuild (an alerts=False growth tick, or any non-SESSION scope)
        must not yank a scrolled-up reader to the bottom — the append-path
        tail-follow guard never covered _rebuild_timeline. Covers the off-by-one
        boundary: a reader parked exactly ONE line above the bottom (the case a
        `>= max - 1` guard wrongly treats as 'at bottom'). A reader actually at
        the bottom still follows the newest line."""
        import json
        import tempfile
        from cccopilot.chat import ChatSession
        from textual.widgets import RichLog
        d = tempfile.mkdtemp(prefix="ccscroll-")
        p = os.path.join(d, "s.jsonl")

        def append_event(i):
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                        "model": "c", "content": [{"type": "tool_use", "id": f"t{i}",
                        "name": "Bash", "input": {"command": f"cmd-{i}", "description": ""}}]}}) + "\n")

        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "sessionId": "s", "cwd": "/x",
                                "message": {"role": "user", "content": "go"}}) + "\n")
        for i in range(60):
            append_event(i)
        sess = ChatSession(p, alerts=False, persist=False)
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            rl = app.query_one("#timeline-log", RichLog)
            self.assertGreater(rl.max_scroll_y, 3)          # comfortably scrollable
            # (a) a deep scroll-up survives a rebuild
            rl.scroll_to(y=2, animate=False)
            await pilot.pause()
            append_event(100)                               # new activity lands…
            app._tick_refresh()                             # …alerts=False → FULL rebuild
            await pilot.pause()
            self.assertLessEqual(rl.scroll_offset.y, 3)     # stayed up top, NOT yanked
            # (b) off-by-one boundary: parked exactly ONE line above the bottom
            parked = rl.max_scroll_y - 1
            rl.scroll_to(y=parked, animate=False)
            await pilot.pause()
            append_event(101)                               # bottom moves down by one…
            app._tick_refresh()                             # …a `>= max-1` guard yanks here
            await pilot.pause()
            self.assertLess(rl.scroll_offset.y, rl.max_scroll_y)   # held above the bottom
            # (c) a reader at the true bottom still follows the newest line
            rl.scroll_end(animate=False)
            await pilot.pause()
            append_event(102)
            app._tick_refresh()
            await pilot.pause()
            self.assertEqual(rl.scroll_offset.y, rl.max_scroll_y)   # followed exactly to bottom

    async def test_timeline_height_clamped_to_small_screen(self):
        import tempfile
        from cccopilot import prefs as PREFS
        old = os.environ.get("CC_COPILOT_STATE_DIR")
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="ccclamp-")
        os.environ.pop("CC_COPILOT_TIMELINE_HEIGHT", None)
        try:
            PREFS.set("timeline_height", 24)                       # bigger than the screen
            app = tui.Cockpit(self._session("sess-A"), poll=999, alerts=False)
            async with app.run_test(size=(80, 20)) as pilot:
                await pilot.pause()
                self.assertLessEqual(app._timeline_height, 10)     # clamped to leave room
        finally:
            if old is None:
                os.environ.pop("CC_COPILOT_STATE_DIR", None)
            else:
                os.environ["CC_COPILOT_STATE_DIR"] = old

    async def test_auto_refresh_updates_activity_without_manual_refresh(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            with open(sess.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asst("fresh auto activity", 0)) + "\n")
            app._tick_refresh()
            await pilot.pause()
            joined = _timeline_text(app)
        self.assertIn("fresh auto activity", joined)

    async def test_in_flight_answer_stays_with_cockpit_after_evidence_switch(self):
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            origin_store = app.session.store
            origin_st = app.session.st
            b = write([user("task", 100, sessionId="sess-B"), asst("ok", 5)])
            app.session.switch_path(b)                # user changes evidence mid-flight
            app._rebuild_chat()
            await pilot.pause()
            # the answer for the cockpit returns AFTER the evidence switch.
            app._answer_done("q-for-A", "answer-A [L1]", True, origin_st, origin_store)
            await pilot.pause()
        self.assertEqual(origin_store.load_history(),
                         [("user", "q-for-A"), ("assistant", "answer-A [L1]")])
        self.assertIs(app.session.store, origin_store)
        self.assertEqual(app.session.history,
                         [("user", "q-for-A"), ("assistant", "answer-A [L1]")])

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
            comp.text = "/br"; app._slash_update()
            await pilot.press("enter")                  # Enter accepts + runs
            await pilot.pause()
            self.assertEqual(comp.text, "")
            self.assertFalse(app._slash_open)
            comp.text = "/mod"; app._slash_update(); app._slash_complete()
            self.assertEqual(comp.text, "/model ")      # arg command keeps a space
            comp.text = "/mod"; app._slash_update()
            await pilot.press("enter")                  # arg command inserts
            await pilot.pause()
            self.assertEqual(comp.text, "/model ")
            comp.text = "/sc"; app._slash_update()
            self.assertFalse(app._slash_open)           # /scope is hidden in TUI
            comp.text = "hello"; app._slash_update()
            self.assertFalse(app._slash_open)           # non-slash text hides it

    async def test_theme_switcher_is_cockpit_curated(self):
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
            self.assertIn("Cockpit Theme", titles)
            self.assertNotIn("Theme", titles)

            app._meta("/theme graphite")
            await pilot.pause()
            self.assertEqual(app.theme, "graphite")
            self.assertEqual(tui._PAL["bg"],
                             tui.COCKPIT_THEME_SPECS["graphite"]["background"])
            app._meta("/theme signal")
            await pilot.pause()
            self.assertEqual(app.theme, "signal")

    async def test_scope_command_updates_status(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/scope project")
            await pilot.pause()
            self.assertEqual(app.session.scope, "project")
            self.assertIn("watching project", str(app.query_one("#status", Static).content))
            self.assertIn("evidence project", str(app.query_one("#status-header", Static).content))
            joined = _timeline_text(app)
            self.assertIn("project activity", joined)

    async def test_scope_command_selects_session_subset(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        sid = os.path.basename(sess.path)[:-6]
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta(f"/scope multi {sid}")
            await pilot.pause()
            self.assertEqual(app.session.scope, "multi-session")
            self.assertEqual(app.session.scope_sessions, [sid])
            self.assertIn("watching multi-session:1",
                          str(app.query_one("#status", Static).content))
            self.assertIn("evidence multi-session:1",
                          str(app.query_one("#status-header", Static).content))
            joined = _timeline_text(app)
            self.assertIn("multi-session activity", joined)

    async def test_sessions_selection_sets_single_or_multi_evidence(self):
        sess = self._session("sess-A")
        write([user("task b", 100, sessionId="sess-B"), asst("ok", 5)], dir=self.home)
        write([user("task c", 100, sessionId="sess-C"), asst("ok", 5)], dir=self.home)
        refs = sess.sibling_refs()
        ids = [r.session_id for r in refs]
        current = os.path.basename(sess.path)[:-6]
        other = next(sid for sid in ids if sid != current)

        msg = tui._apply_session_selection(sess, refs, [current, other])
        self.assertEqual(sess.scope, tui.SC.MULTI)
        self.assertEqual(sess.scope_sessions, [current, other])
        self.assertIn("2 selected", msg)

        msg = tui._apply_session_selection(sess, refs, [other])
        self.assertEqual(sess.scope, tui.SC.SESSION)
        self.assertEqual(sess.scope_sessions, [])
        self.assertEqual(os.path.basename(sess.path)[:-6], other)
        self.assertIn(other[:8], msg)

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

    async def test_watch_baseline_resets_after_switch(self):
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            first_state = app.session.st
            app._reset_watch_baseline()
            self.assertIs(app._watch_state, first_state)
            b = write([user("task", 100, sessionId="sess-B"), asst("ok", 5)])
            app.session.switch_path(b)
            app._reset_watch_baseline()
            await pilot.pause()
        self.assertEqual(app._watch_path, b)
        self.assertIs(app._watch_state, app.session.st)

    async def test_busy_tick_advances_only_while_busy(self):
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            start = app._busy_frame
            app._tick_busy()
            self.assertEqual(app._busy_frame, start)

            app._busy = True
            app._tick_busy()
            self.assertEqual(app._busy_frame, (start + 1) % len(tui._BUSY_FRAMES))


if __name__ == "__main__":
    unittest.main()
