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

    async def test_neutral_ground_and_agent_blend_accent(self):
        """The cockpit ground is neutral grey (not the old blue-ink), and its
        accent IS the midpoint of the two agents' brand colors — the copilot's
        color is literally the Claude×Codex blend it supervises."""
        spec = tui.COCKPIT_THEME_SPECS["cockpit"]
        self.assertEqual(spec["background"], "#1e1e1e")        # rgb(30,30,30)
        # the main panes (header/timeline/chat) paint with $panel; keep it flush
        # with the ground so what fills the screen IS the asked-for color, not a
        # lighter layer floating over it.
        self.assertEqual(spec["panel"], spec["background"])

        def _rgb(h):
            h = h.lstrip("#")
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

        claude, codex = _rgb(tui._AGENT_HEX["claude"]), _rgb(tui._AGENT_HEX["codex"])
        mid = tuple(round((a + b) / 2) for a, b in zip(claude, codex))
        self.assertEqual(_rgb(spec["accent"]), mid)            # #807ea6 = blend

    async def test_agent_hex_maps_brands_and_falls_back_to_accent(self):
        self.assertEqual(tui._agent_hex("claude"), "#cb7d5b")
        self.assertEqual(tui._agent_hex("Codex"), "#347ff2")   # case-insensitive
        # unknown / missing agent shows the copilot's own accent, not a stray hue
        self.assertEqual(tui._agent_hex("gemini"), tui._PAL["accent"])
        self.assertEqual(tui._agent_hex(""), tui._PAL["accent"])

    async def test_activity_line_paints_agent_label_in_brand_hue(self):
        """The timeline `agent` label is styled with the passed-in brand hue, so a
        Codex row glows blue and a Claude row rust even in a mixed multi-session
        view (each row carries its own session's color)."""
        rec = types.SimpleNamespace(
            kind="agent_text", hhmm="10:00", text="done", housekeeping=False,
            tool_name=None, tool_input=None, is_error=False)
        line = tui._activity_line(rec, "#347ff2")
        agent_span = next(s for s in line.spans if line.plain[s.start:s.end] == "agent")
        self.assertEqual(agent_span.style, "#347ff2")

    async def test_scoped_prefix_preserves_agent_brand_span(self):
        """In a multi-session timeline the sid-prefixed row must keep its
        per-agent `agent` hue — `_prefixed_activity_line` preserves the spans
        instead of flattening the row to one muted color."""
        rec = types.SimpleNamespace(
            kind="agent_text", hhmm="10:00", text="done", housekeeping=False,
            tool_name=None, tool_input=None, is_error=False)
        line = tui._activity_line(rec, "#347ff2")          # Codex blue
        prefixed = tui._prefixed_activity_line("ab12cd34", line)
        agent_span = next(s for s in prefixed.spans
                          if prefixed.plain[s.start:s.end] == "agent")
        self.assertEqual(agent_span.style, "#347ff2")


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

    async def test_watched_agent_label_uses_brand_color(self):
        """End to end: watching a Claude session paints its identity spans (the
        header's "<agent> session" and the status-line `watching …`) in Claude's
        brand rust, not the generic accent."""
        from textual.widgets import Static
        sess = self._session("sess-A")
        self.assertEqual(tui._agent_of(sess), "claude")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(92, 30)) as pilot:
            await pilot.pause()
            head = app.query_one("#status-header", Static).content
            hspan = next(s for s in head.spans
                         if head.plain[s.start:s.end] == "claude session")
            self.assertEqual(hspan.style, "#cb7d5b")
            status = app.query_one("#status", Static).content
            wspan = next(s for s in status.spans
                         if "watching claude session" in status.plain[s.start:s.end])
            self.assertEqual(wspan.style, "#cb7d5b")

    async def test_chat_and_timeline_panes_align(self):
        """Chat and timeline are the same (full) width so their right-edge
        scrollbars line up, both use a thin 1-cell vertical bar, and the chat
        shares the timeline's $panel background (one continuous surface, not a
        separate color block)."""
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(92, 30)) as pilot:
            await pilot.pause()
            chat = app.query_one("#chat")
            timeline = app.query_one("#timeline")
            tlog = app.query_one("#timeline-log")
            # full-width parity → right edges (and the scrollbars on them) align
            self.assertEqual(chat.outer_size.width, timeline.outer_size.width)
            self.assertEqual(chat.outer_size.width, app.size.width)
            # a thin 1-cell vertical scrollbar on both panes
            self.assertEqual(chat.styles.scrollbar_size_vertical, 1)
            self.assertEqual(tlog.styles.scrollbar_size_vertical, 1)
            # chat blends with the timeline (same panel background)
            self.assertEqual(chat.styles.background, tlog.styles.background)

    async def test_cockpit_since_renders_grounded_recap_async(self):
        """/since in the cockpit narrates the cited delta on a worker thread (no UI
        freeze) and renders the recap + evidence; --raw stays the instant delta."""
        import json
        import tempfile
        from textual.widgets import Collapsible, Static
        from cccopilot import narrate as N
        from cccopilot.chat import ChatSession
        real_recap, real_avail = N.recap_since, N.available
        N.available = lambda be=None: True
        N.recap_since = lambda text, model=None, backend=None: "RECAP_NARRATIVE [L4]"
        try:
            d = tempfile.mkdtemp(prefix="cctsince-")
            p = os.path.join(d, "s.jsonl")
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "user", "sessionId": "s", "cwd": "/x",
                                    "message": {"role": "user", "content": "go"}}) + "\n")
                for i in range(4):
                    f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                            "model": "c", "content": [{"type": "tool_use", "id": f"t{i}",
                            "name": "Bash", "input": {"command": f"cmd-{i}", "description": ""}}]}}) + "\n")
                    f.write(json.dumps({"type": "user", "message": {"role": "user",
                            "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                         "content": "ok"}]}}) + "\n")
            sess = ChatSession(p, backend="codex", alerts=False, persist=False)
            app = tui.Cockpit(sess, poll=999, alerts=False)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/since 30m")                       # starts the recap worker
                await app.workers.wait_for_complete()         # let the thread finish
                await pilot.pause()                           # process call_from_thread
                blob = "\n".join(str(getattr(s, "content", "") or "")
                                 for s in app.query("#chat Static"))
                self.assertIn("RECAP_NARRATIVE", blob)        # the narrative landed
                self.assertIn("evidence", blob)               # cited delta beneath it
                self.assertFalse(app._busy)                   # spinner cleared
        finally:
            N.recap_since, N.available = real_recap, real_avail

    async def test_cockpit_since_recap_dropped_after_evidence_switch(self):
        """If the user switches evidence while a /since recap runs, the result —
        whose citations are about the OLD session — must be dropped, not rendered
        under the new transcript, AND the last-look marker must NOT advance (else
        the dropped delta is lost forever)."""
        import json
        import tempfile
        from textual.widgets import Static
        from cccopilot import narrate as N, lastlook as LL
        from cccopilot.chat import ChatSession, _now_iso
        real_recap, real_avail = N.recap_since, N.available
        saved_state = os.environ.get("CC_COPILOT_STATE_DIR")
        os.environ["CC_COPILOT_STATE_DIR"] = tempfile.mkdtemp(prefix="cctswstate-")
        try:
            d = tempfile.mkdtemp(prefix="cctswitch-")
            p = os.path.join(d, "s.jsonl")
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "user", "sessionId": "s", "cwd": "/x",
                                    "message": {"role": "user", "content": "go"}}) + "\n")
                for i in range(4):
                    f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                            "model": "c", "content": [{"type": "tool_use", "id": f"t{i}",
                            "name": "Bash", "input": {"command": f"cmd-{i}", "description": ""}}]}}) + "\n")
            sess = ChatSession(p, backend="codex", alerts=False, persist=False)
            key = sess._lastlook_key()
            LL.mark(key, 1, "", _now_iso())                  # early mark → real delta
            app = tui.Cockpit(sess, poll=999, alerts=False)
            N.available = lambda be=None: True
            # the model call "takes a while" during which the user switches evidence
            def recap_then_switch(text, model=None, backend=None):
                app.session.path = "/some/other/session.jsonl"   # evidence changed
                return "STALE_RECAP [L4]"
            N.recap_since = recap_then_switch
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/since")                          # last-look path
                await app.workers.wait_for_complete()
                await pilot.pause()
                blob = "\n".join(str(getattr(s, "content", "") or "")
                                 for s in app.query("#chat Static"))
                self.assertNotIn("STALE_RECAP", blob)         # dropped, not mis-rendered
                self.assertFalse(app._busy)                   # spinner still cleared
                self.assertEqual(int(LL.get(key)["line"]), 1)  # marker preserved (delta survives)
        finally:
            N.recap_since, N.available = real_recap, real_avail
            if saved_state is None:
                os.environ.pop("CC_COPILOT_STATE_DIR", None)
            else:
                os.environ["CC_COPILOT_STATE_DIR"] = saved_state

    async def test_since_recap_dropped_when_conversation_changed(self):
        """/new or /resume keeps the same transcript (same evidence signature) but a
        fresh conversation store; a pending recap must drop, not render into the new
        chat — the origin captures the store, not just the evidence."""
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            # origin: current evidence, but a DIFFERENT conversation store (as if the
            # user ran /new while the recap was on the worker thread)
            stale_origin = (app._evidence_sig(), object())
            app._busy = True
            app._since_done("/since 30m", "STALE_RECAP [L4]", stale_origin, lambda: None)
            await pilot.pause()
            blob = "\n".join(str(getattr(s, "content", "") or "")
                             for s in app.query("#chat Static"))
            self.assertNotIn("STALE_RECAP", blob)          # dropped on store change
            self.assertFalse(app._busy)                    # spinner cleared either way

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

    async def test_same_evidence_preserves_switch_lands_on_newest(self):
        """Auto-detected keep_scroll: a rebuild whose evidence identity is unchanged
        (poll/theme/refresh, re-observe via _refresh_scope_view, a no-op /scope)
        holds the reader's scroll; an evidence switch (different session/scope) lands
        on the newest line, so a freshly-selected session doesn't open mid-scroll."""
        import json
        import tempfile
        from cccopilot.chat import ChatSession
        from textual.widgets import RichLog
        d = tempfile.mkdtemp(prefix="ccswitch-")
        p = os.path.join(d, "s.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "sessionId": "s", "cwd": "/x",
                                "message": {"role": "user", "content": "go"}}) + "\n")
            for i in range(60):
                f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                        "model": "c", "content": [{"type": "tool_use", "id": f"t{i}",
                        "name": "Bash", "input": {"command": f"cmd-{i}", "description": ""}}]}}) + "\n")
        sess = ChatSession(p, alerts=False, persist=False)
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            rl = app.query_one("#timeline-log", RichLog)
            self.assertGreater(rl.max_scroll_y, 3)
            # same evidence (signature unchanged since mount) -> scroll held
            rl.scroll_to(y=2, animate=False)
            await pilot.pause()
            app._rebuild_timeline()
            await pilot.pause()
            self.assertLessEqual(rl.scroll_offset.y, 3)
            # P3: _refresh_scope_view on unchanged evidence (e.g. re-observe) holds too
            rl.scroll_to(y=2, animate=False)
            await pilot.pause()
            app._refresh_scope_view()
            await pilot.pause()
            self.assertLessEqual(rl.scroll_offset.y, 3)
            # an evidence switch (the prior signature differs) lands on newest
            rl.scroll_to(y=2, animate=False)
            await pilot.pause()
            app._timeline_sig = ("__other_evidence__", "", ())
            app._rebuild_timeline()
            await pilot.pause()
            self.assertGreaterEqual(rl.scroll_offset.y, rl.max_scroll_y)

    async def test_keep_scroll_preserves_horizontal_pan_at_bottom(self):
        """A same-session refresh while tailing must keep a horizontal pan — a bare
        scroll_end (x_axis=True) would snap a panned-across long line to column 0
        every poll. An evidence switch instead resets the pan to the start. The long
        rows live in the EVIDENCE (JSONL), so they survive _rebuild_timeline's
        clear-and-reseed and the pan assertion is real (not a deferred-clamp fluke)."""
        import json
        import tempfile
        from cccopilot.chat import ChatSession
        from textual.widgets import RichLog
        d = tempfile.mkdtemp(prefix="ccpan-")
        p = os.path.join(d, "s.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "sessionId": "s", "cwd": "/x",
                                "message": {"role": "user", "content": "go"}}) + "\n")
            for i in range(40):                                  # long commands in the evidence
                f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                        "model": "c", "content": [{"type": "tool_use", "id": f"t{i}",
                        "name": "Bash", "input": {"command": f"echo row {i} " + ("y" * 120),
                                                  "description": ""}}]}}) + "\n")
        sess = ChatSession(p, alerts=False, persist=False)
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            rl = app.query_one("#timeline-log", RichLog)
            self.assertGreater(rl.max_scroll_x, 30)              # pannable from the evidence
            rl.scroll_to(x=30, y=rl.max_scroll_y, animate=False)  # at bottom AND panned
            await pilot.pause()
            self.assertEqual(rl.scroll_offset.x, 30)
            self.assertEqual(rl.scroll_offset.y, rl.max_scroll_y)
            app._rebuild_timeline()                              # same evidence: poll/theme/refresh
            await pilot.pause()
            self.assertGreater(rl.max_scroll_x, 30)             # long rows survived the reseed
            self.assertEqual(rl.scroll_offset.x, 30)            # pan KEPT (x_axis=False)
            self.assertEqual(rl.scroll_offset.y, rl.max_scroll_y)  # still tailing
            # an evidence switch resets the horizontal pan to the start
            rl.scroll_to(x=30, y=rl.max_scroll_y, animate=False)
            await pilot.pause()
            app._timeline_sig = ("__other_evidence__", "", ())   # prior evidence differed
            app._rebuild_timeline()
            await pilot.pause()
            self.assertEqual(rl.scroll_offset.x, 0)              # pan reset for new evidence

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

    async def test_status_bar_reflows_to_narrow_keeping_every_field(self):
        """In a narrow sidebar the status bar stacks into rows instead of cropping
        — every datum that's on the wide single line survives into the narrow
        stack (the user's hard requirement: all details stay visible)."""
        from cccopilot import context as EC, assess as A
        sess = self._session("sess-A")
        sess.model = "gpt-5"
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(120, 26)) as pilot:
            await pilot.pause()
            app._ctx_stats = EC.ContextStats(
                estimated_tokens=1200, raw_tokens=1100, project_tokens=4000,
                chat_tokens=800, memory_tokens=120, index_tokens=90,
                budget_tokens=200000, truncated=True)
            app._out_tokens = 300
            verdict = A.assess(sess.st).verdict.upper()

            wide = app._status_text(200).plain
            narrow = app._status_text(42).plain
            brutal = app._status_text(30).plain

            # wide is one dense line; narrow reflows to several rows
            self.assertNotIn("\n", wide)
            self.assertGreaterEqual(narrow.count("\n"), 4)

            # every datum on the wide line must survive into the narrow stack —
            # the HUD parts (split from EC.format_hud) are the easiest to lose.
            data = ["copilot codex:gpt-5", "claude session", "idle", verdict,
                    "ctx ~1.2k / 200k", "out ~300", "raw 1.1k", "project 4k",
                    "chat 800", "memory 120", "index 90", "trimmed"]
            for tok in data:
                self.assertIn(tok, wide, f"{tok} missing from wide line")
                self.assertIn(tok, narrow, f"{tok} dropped when narrow")

            # PIN invariant: even at a brutal width the verdict badge and the full
            # HUD are still present (badge just demotes to its own row).
            self.assertIn(verdict, brutal)
            self.assertIn("index 90", brutal)

    async def test_status_bar_narrow_rows_render_without_clipping(self):
        """Codex P2 regression: narrow rows are width-packed so they don't
        soft-wrap past the height cap. Reads the RENDERED strips (not .plain,
        which clipping wouldn't change) to prove every field is actually on
        screen, and that the rendered height equals the row count (no wrap)."""
        from textual.widgets import Static
        from cccopilot import context as EC, assess as A
        sess = self._session("sess-A")
        sess.model = "gpt-5"
        app = tui.Cockpit(sess, poll=999, alerts=False)
        # narrow (32) but tall (44) so the terminal height is not the limiter
        async with app.run_test(size=(32, 44)) as pilot:
            await pilot.pause()
            app._ctx_stats = EC.ContextStats(
                estimated_tokens=1200, raw_tokens=1100, project_tokens=4000,
                chat_tokens=800, memory_tokens=120, index_tokens=90,
                budget_tokens=200000, truncated=True)
            app._out_tokens = 300
            app._update_status()
            await pilot.pause()
            verdict = A.assess(sess.st).verdict.upper()
            status = app.query_one("#status", Static)
            # rendered height == logical row count → packing prevented soft-wrap
            self.assertEqual(status.region.height,
                             app._status_text(30).plain.count("\n") + 1)
            self.assertLessEqual(status.region.height, 12)      # within the CSS cap
            strips = app.screen._compositor.render_strips()
            y0 = status.region.y
            visible = "\n".join(strips[y].text for y in
                                range(y0, min(y0 + status.region.height, len(strips))))
            for tok in (verdict, "copilot codex:gpt-5", "claude session", "idle",
                        "ctx ~1.2k / 200k", "out ~300", "raw 1.1k", "project 4k",
                        "chat 800", "memory 120", "index 90", "trimmed"):
                self.assertIn(tok, visible, f"{tok} CLIPPED (not on screen) at 32 cols")

    async def test_select_mode_releases_mouse_and_shows_banner(self):
        """Ctrl+S / `/select` releases the mouse to the terminal (so native
        drag-select + ⌘C work) and shows a banner; toggling back restores it."""
        from textual.widgets import Static
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            calls = []
            # the headless test driver lacks these; inject stubs to observe the toggle
            app._driver._enable_mouse_support = lambda: calls.append("enable")
            app._driver._disable_mouse_support = lambda: calls.append("disable")

            self.assertFalse(app._select_mode)
            self.assertIn("/select", [c for c, *_ in tui._SLASH_CMDS])
            app._meta("/select")                       # enter via the slash command
            await pilot.pause()
            self.assertTrue(app._select_mode)
            self.assertEqual(calls, ["disable"])       # mouse handed to the terminal
            self.assertIn("SELECT MODE",
                          str(app.query_one("#status", Static).content))

            app.action_toggle_select_mode()            # exit via the key action
            await pilot.pause()
            self.assertFalse(app._select_mode)
            self.assertEqual(calls, ["disable", "enable"])   # mouse restored
            self.assertNotIn("SELECT MODE",
                             str(app.query_one("#status", Static).content))

    async def test_status_bar_history_only_stacks_when_narrow(self):
        from cccopilot import context as EC  # noqa
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(100, 26)) as pilot:
            await pilot.pause()
            app.session.st = None                       # transcript gone
            wide = app._status_text(200).plain
            narrow = app._status_text(40).plain
            self.assertNotIn("\n", wide)
            self.assertGreaterEqual(narrow.count("\n"), 2)
            for tok in ("history-only", "transcript gone", "copilot codex",
                        "claude session"):
                self.assertIn(tok, narrow, f"{tok} dropped when narrow")

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
