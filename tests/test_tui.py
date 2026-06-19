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
class TestTurnHeader(unittest.TestCase):
    """The per-message header puts the role on the left and the dim time hard
    against the right edge, at any width."""

    def _render(self, renderable, width=40):
        import io
        from rich.console import Console
        c = Console(file=io.StringIO(), width=width, color_system=None)
        c.print(renderable)
        return c.file.getvalue()

    def test_head_grid_label_left_time_right(self):
        out = self._render(tui.Cockpit._head_grid("you", "white", "14:32"))
        self.assertIn("you", out)
        self.assertIn("14:32", out)
        self.assertLess(out.index("you"), out.index("14:32"))     # time on the right
        self.assertTrue(out.rstrip().endswith("14:32"))           # hard against the edge

    def test_head_grid_tolerates_missing_time(self):
        out = self._render(tui.Cockpit._head_grid("copilot", "white", ""))
        self.assertIn("copilot", out)                              # no crash, no '--:--'


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
            self.assertFalse(app.query_one("#session-hud").can_focus)

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

    async def test_filter_handles_rich_labels(self):
        from rich.text import Text
        from textual.widgets import OptionList, Static

        chosen = []

        class Harness(App):
            def compose(self):
                yield Static("root")

        app = Harness()
        async with app.run_test() as pilot:
            picker = tui.Picker("pick", [
                (Text("Claude", style="#cb7d5b"), "claude"),
                (Text("DeepSeek", style="#8b5cf6"), "deepseek"),
            ])
            await app.push_screen(picker, chosen.append)
            await pilot.pause()

            await pilot.press("s", "e")
            await pilot.pause()
            ol = picker.query_one("#picker-list", OptionList)
            self.assertEqual(ol.option_count, 1)
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(chosen, ["deepseek"])

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

    async def test_model_picker_brand_colors_come_from_onboarding_choices(self):
        self.assertEqual(tui._backend_choice_hex("claude"), "#cb7d5b")
        self.assertEqual(tui._backend_choice_hex("codex"), "#347ff2")
        self.assertEqual(tui._backend_choice_hex("deepseek"), "#8b5cf6")
        self.assertEqual(tui._backend_choice_hex("ollama"), "")

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
                     ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY",
                      "CC_COPILOT_CONFIG", "CLAUDE_CODE_SESSION_ID",
                      "CLAUDE_SESSION_ID", "CLAUDE_CONFIG_DIR", "CODEX_HOME",
                      "CODEX_THREAD_ID", "CODEX_SESSION_ID",
                      "CODEX_CONVERSATION_ID", "CODEX_ROLLOUT_ID",
                      "CODEX_CI", "CODEX_MANAGED_BY_NPM")}
        for k in self._env:
            os.environ.pop(k, None)
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
        N.recap_since = lambda text, model=None, backend=None, instruction="": "RECAP_NARRATIVE [L4]"
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

    async def test_cockpit_now_renders_grounded_next_step_async(self):
        """/now recommends the next step on a worker thread (no UI freeze) and
        renders the recommendation with the deterministic anchor beneath it."""
        from cccopilot import narrate as N
        real_next, real_avail = N.next_step_brief, N.available
        N.available = lambda be=None: True
        N.next_step_brief = lambda text, model=None, backend=None, instruction="": "DO_THIS_NEXT [L3]"
        try:
            sess = self._session("sess-A")
            app = tui.Cockpit(sess, poll=999, alerts=False)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/now")                             # starts the worker
                await app.workers.wait_for_complete()         # let the thread finish
                await pilot.pause()                           # process call_from_thread
                blob = "\n".join(str(getattr(s, "content", "") or "")
                                 for s in app.query("#chat Static"))
                self.assertIn("DO_THIS_NEXT", blob)           # the recommendation landed
                self.assertIn("next step", blob)              # the heading
                self.assertIn("deterministic next-step", blob)  # grounded anchor beneath
                self.assertFalse(app._busy)                   # spinner cleared
        finally:
            N.next_step_brief, N.available = real_next, real_avail

    async def test_cockpit_goal_renders_grounded_goal_async(self):
        """/goal drafts a paste-ready agent goal on a worker thread and keeps the
        deterministic fallback under it as a grounded anchor."""
        from cccopilot import narrate as N
        from textual.widgets import Markdown
        real_goal, real_avail = N.goal_brief, N.available
        N.available = lambda be=None: True
        N.goal_brief = lambda text, model=None, backend=None, instruction="": (
            "```text\n/goal MODEL_GOAL\n```\n\nWhy this goal\n- observed [L1]"
        )
        try:
            sess = self._session("sess-A")
            app = tui.Cockpit(sess, poll=999, alerts=False)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/goal prefer tests")
                await app.workers.wait_for_complete()
                await pilot.pause()
                srcs = "\n".join(getattr(m, "source", "")
                                 for m in app.query_one("#chat").query(Markdown))
                self.assertIn("# 🎯 agent goal", srcs)
                self.assertIn("MODEL_GOAL", srcs)
                self.assertIn("deterministic fallback", srcs)
                self.assertFalse(app._busy)
        finally:
            N.goal_brief, N.available = real_goal, real_avail

    async def test_meta_results_render_as_inline_markdown_not_a_box(self):
        """/now (and its /since, /brief siblings) render through the Markdown
        widget — rendered headings, no collapsible box, no literal '#'/'**' left
        sitting in a raw Static."""
        from cccopilot import narrate as N
        from textual.widgets import Markdown, Collapsible
        real_next, real_avail = N.next_step_brief, N.available
        N.available = lambda be=None: True
        N.next_step_brief = lambda text, model=None, backend=None, instruction="": "DO_THIS_NEXT [L3]"
        try:
            sess = self._session("sess-A")
            app = tui.Cockpit(sess, poll=999, alerts=False)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/now")
                await app.workers.wait_for_complete()
                await pilot.pause()
                mds = app.query_one("#chat").query(Markdown)
                srcs = "\n".join(getattr(m, "source", "") for m in mds)
                self.assertIn("# 🧭 next step", srcs)      # heading went to a Markdown widget
                self.assertIn("DO_THIS_NEXT", srcs)
                self.assertEqual(len(app.query(Collapsible)), 0)  # no box layer
        finally:
            N.next_step_brief, N.available = real_next, real_avail

    async def test_now_without_backend_renders_text_not_markdown(self):
        # the deterministic next-step is plain text with indented `also:` siblings;
        # it must NOT route through Markdown (which flattens the indentation).
        from cccopilot import narrate as N
        from textual.widgets import Markdown
        real_avail = N.available
        N.available = lambda be=None: False
        try:
            sess = self._session("sess-A")
            app = tui.Cockpit(sess, poll=999, alerts=False)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/now")
                await pilot.pause()
                self.assertEqual(len(app.query_one("#chat").query(Markdown)), 0)
                self.assertGreaterEqual(len(app.query("#chat .role-meta")), 1)
        finally:
            N.available = real_avail

    async def test_status_and_target_render_in_chat(self):
        """/status pulls the fleet board (independent of pinned evidence); /target
        shows the current cockpit readout."""
        from cccopilot import chat as C
        real = C.render_fleet
        C.render_fleet = lambda cwd, **k: ("cc-copilot status — DEMO\n 🔴 stalled", 1)
        try:
            sess = self._session("sess-A")
            app = tui.Cockpit(sess, poll=999, alerts=False)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/target")
                await pilot.pause()
                app._meta("/status")
                await pilot.pause()
                blob = "\n".join(str(getattr(s, "content", "") or "")
                                 for s in app.query("#chat Static"))
                self.assertIn("cockpit:", blob)                   # /target readout
                self.assertIn("cc-copilot status — DEMO", blob)   # /status board
        finally:
            C.render_fleet = real

    async def test_cockpit_now_without_backend_is_instant_deterministic(self):
        """With no backend, /now shows the deterministic next-step immediately and
        never enters the busy/worker path."""
        from cccopilot import narrate as N
        real_avail = N.available
        N.available = lambda be=None: False
        try:
            sess = self._session("sess-A")
            app = tui.Cockpit(sess, poll=999, alerts=False)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/now")
                await pilot.pause()
                blob = "\n".join(str(getattr(s, "content", "") or "")
                                 for s in app.query("#chat Static"))
                self.assertIn("→", blob)                      # deterministic decision shown
                self.assertFalse(app._busy)                   # never went busy
        finally:
            N.available = real_avail

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
            def recap_then_switch(text, model=None, backend=None, instruction=""):
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
            hud = app.query_one("#session-hud", Static)
            composer = app.query_one("#composer", tui.Composer)
            self.assertLess(hud.region.y, composer.region.y)
        self.assertIn("project", header)
        # single-session header now names the agent it's watching
        self.assertIn("evidence claude session", header)
        self.assertIn("sess-A", header)
        self.assertIn("activity current session", header)
        hud_text = str(hud.content)
        self.assertIn("attached session", hud_text)
        self.assertIn("task", hud_text)
        self.assertIn("claude session task", hud_text)
        self.assertIn("sess-A", hud_text)
        self.assertLess(hud_text.index("claude session"), hud_text.index("task"))
        self.assertLess(hud_text.index("task"), hud_text.index("sess-A"))

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

    async def test_in_flight_answer_records_origin_metadata_after_switch(self):
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            origin_store = app.session.store
            origin_st = app.session.st
            origin_path = os.path.abspath(app.session.path)
            app.backend = app.session.backend = "openai"
            app.model = app.session.model = "gpt-5.5"
            app.session.scope = tui.SC.MULTI
            app.session.scope_sessions = ["sess-A"]
            origin = app._answer_origin(origin_st, origin_store)

            b = write([user("task", 100, sessionId="sess-B"), asst("ok", 5)],
                      dir=self.home)
            app.session.switch_path(b)
            app.backend = app.session.backend = "codex"
            app.model = app.session.model = None
            app.session.scope = tui.SC.SESSION
            app.session.scope_sessions = []
            app._rebuild_chat()
            await pilot.pause()

            app._answer_done("q-origin", "answer-origin [L1]", True,
                             origin_st, origin_store, origin=origin)
            await pilot.pause()

        turn = origin_store._load_turns()[-1]
        self.assertEqual(turn["backend"], "openai")
        self.assertEqual(turn["model"], "gpt-5.5")
        self.assertEqual(turn["src"]["scope"], tui.SC.MULTI)
        self.assertEqual(turn["src"]["scope_sessions"], ["sess-A"])
        self.assertEqual(turn["src"]["transcript"], origin_path)
        header = origin_store.header()
        self.assertEqual(header.scope, tui.SC.SESSION)
        self.assertEqual(header.scope_sessions, [])
        self.assertEqual(header.transcript, os.path.abspath(b))

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

    async def test_scope_mentions_autocomplete_and_apply(self):
        from textual.widgets import OptionList, Static
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            comp = app.query_one("#composer", tui.Composer)
            ol = app.query_one("#slash", OptionList)

            comp.text = "@"
            app._slash_update()
            self.assertTrue(app._mention_open)
            self.assertFalse(app._slash_open)
            self.assertEqual([ol.get_option_at_index(i).id for i in range(ol.option_count)],
                             ["@session", "@sessions", "@project"])

            comp.text = "@pro"
            app._slash_update()
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.session.scope, "project")
            self.assertEqual(comp.text, "")
            self.assertFalse(app._mention_open)
            self.assertIn("watching project",
                          str(app.query_one("#status", Static).content))

            comp.text = "@session"
            app._slash_update()
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.session.scope, "session")

            called = []
            app.action_sessions = lambda: called.append(True)
            self.assertTrue(app._apply_scope_mention("@sessions"))
            self.assertEqual(called, [True])

            tui.SG.save("Release", "project")
            comp.text = "@rel"
            app._slash_update()
            self.assertTrue(app._mention_open)
            self.assertEqual([ol.get_option_at_index(i).id for i in range(ol.option_count)],
                             ["@Release"])
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.session.scope, "project")

            comp.text = "compare @project with current session"
            app._slash_update()
            self.assertFalse(app._mention_open)
            self.assertFalse(ol.display)

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

    async def test_ctrl_n_navigates_open_picker(self):
        """Ctrl+N moves the highlight down inside an open picker (Emacs-style nav);
        the picker stops the event."""
        from textual.widgets import OptionList
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(92, 30)) as pilot:
            await pilot.pause()
            picker = tui.Picker("pick", [("Alpha", "a"), ("Beta", "b"), ("Gamma", "g")])
            await app.push_screen(picker)
            await pilot.pause()
            ol = picker.query_one("#picker-list", OptionList)
            self.assertEqual(ol.highlighted, 0)
            await pilot.press("ctrl+n")
            await pilot.pause()
            self.assertEqual(ol.highlighted, 1)        # picker navigated

    async def test_ctrl_y_copies_selection_and_ctrl_c_stays_quit(self):
        """Ctrl+Y / /copy copies the current text selection (clean text → clipboard
        via OSC 52); with nothing selected it just says so. Ctrl+C is bound to quit,
        never copy — so quitting is never ambiguous. Replaces the removed select-mode."""
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            copied = []
            app._put_on_clipboard = lambda t: copied.append(t)
            app.clear_selection = lambda: None
            # a live selection → copy it
            app.screen.get_selected_text = lambda: "hello world"
            app.action_copy_selection()
            self.assertEqual(copied, ["hello world"])
            # nothing selected → no copy, no crash
            copied.clear()
            app.screen.get_selected_text = lambda: ""
            app.action_copy_selection()
            self.assertEqual(copied, [])
        actions = {b.key: b.action for b in tui.Cockpit.BINDINGS}
        self.assertEqual(actions.get("ctrl+c"), "quit")            # quit, never copy
        self.assertEqual(actions.get("ctrl+y"), "copy_selection")  # copy is its own key

    async def test_ctrl_y_keypress_fires_copy_from_the_focused_composer(self):
        """Regression: a real Ctrl+Y keypress must trigger copy while the composer
        (a TextArea) has focus. The composer swallows ctrl+y, so the binding needs
        priority to win — a non-priority binding silently never fires."""
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertTrue(app.query_one("#composer").has_focus)   # composer is focused
            fired = []
            app.action_copy_selection = lambda: fired.append(1)
            await pilot.press("ctrl+y")
            await pilot.pause()
            self.assertEqual(fired, [1])                            # binding actually fired

    async def test_tip_line_prompts_ctrl_y_when_text_is_selected(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            tip = app.query_one("#tip", Static)
            app._current_tip = "a normal tip"
            # a live selection → the contextual copy prompt replaces the tip
            app.screen.get_selected_text = lambda: "some selected text"
            app._render_tip()
            self.assertIn("Ctrl+Y to copy", str(tip.content))
            # Textual's TextSelected event paints it immediately too
            app.on_text_selected(None)
            self.assertIn("Ctrl+Y to copy", str(tip.content))
            # selection cleared → back to the rotating tip
            app.screen.get_selected_text = lambda: ""
            app._render_tip()
            self.assertIn("a normal tip", str(tip.content))
            self.assertNotIn("Ctrl+Y to copy", str(tip.content))

    async def test_put_on_clipboard_uses_osc52_and_a_local_command(self):
        """The clipboard write is belt-and-suspenders: OSC 52 (for SSH/tmux) AND a
        local command (pbcopy/…), since OSC 52 no-ops on e.g. macOS Terminal.app."""
        import shutil as _shutil
        old_tmux = os.environ.pop("TMUX", None)
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                osc, ran = [], []
                app.copy_to_clipboard = lambda t: osc.append(t)
                real_which, real_run = _shutil.which, tui.subprocess.run
                _shutil.which = lambda name: "/bin/pbcopy" if name == "pbcopy" else None
                tui.subprocess.run = lambda argv, **k: ran.append((argv, k.get("input")))
                try:
                    app._put_on_clipboard("grab me")
                finally:
                    _shutil.which, tui.subprocess.run = real_which, real_run
        finally:
            if old_tmux is not None:
                os.environ["TMUX"] = old_tmux
        self.assertEqual(osc, ["grab me"])             # OSC 52 attempted (remote/tmux)
        self.assertEqual(ran[0][0], ["pbcopy"])        # local command attempted…
        self.assertEqual(ran[0][1], b"grab me")        # …with the text piped to stdin

    async def test_put_on_clipboard_uses_tmux_clipboard_paths_inside_tmux(self):
        import shutil as _shutil
        old_tmux = os.environ.get("TMUX")
        os.environ["TMUX"] = "/tmp/tmux-501/default,123,0"
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                osc, ran, writes = [], [], []
                app.copy_to_clipboard = lambda t: osc.append(t)
                app._write_terminal_sequence = lambda seq: writes.append(seq)
                real_which, real_run = _shutil.which, tui.subprocess.run
                _shutil.which = lambda name: "/bin/tmux" if name == "tmux" else None
                tui.subprocess.run = lambda argv, **k: ran.append((argv, k.get("input")))
                try:
                    app._put_on_clipboard("remote tmux")
                finally:
                    _shutil.which, tui.subprocess.run = real_which, real_run
        finally:
            if old_tmux is None:
                os.environ.pop("TMUX", None)
            else:
                os.environ["TMUX"] = old_tmux
        self.assertEqual(osc, ["remote tmux"])          # plain OSC 52 still attempted
        self.assertEqual(ran[0][0], ["tmux", "load-buffer", "-w", "-"])
        self.assertEqual(ran[0][1], b"remote tmux")
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0].startswith("\x1bPtmux;\x1b\x1b]52;c;"))
        self.assertTrue(writes[0].endswith("\x1b\\"))

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
            hud = str(app.query_one("#session-hud", Static).content)
            self.assertIn("attached sessions", hud)
            self.assertIn("project", hud)
            self.assertIn("all 1", hud)
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
            hud = str(app.query_one("#session-hud", Static).content)
            self.assertIn("attached sessions", hud)
            self.assertIn("multi-session", hud)
            self.assertIn("1 selected of 1", hud)
            joined = _timeline_text(app)
            self.assertIn("multi-session activity", joined)

    async def test_multi_session_activity_tabs_switch_between_all_and_sessions(self):
        from cccopilot.chat import ChatSession
        p1 = write([user("alpha task", 100, sessionId="sess-A"),
                    asst("alpha-result", 5)], dir=self.home)
        p2 = write([user("beta task", 100, sessionId="sess-B"),
                    asst("beta-result", 4)], dir=self.home)
        sess = ChatSession(p1, backend="codex", alerts=False)
        sess.refresh()
        refs = sess.sibling_refs()
        ids = [r.session_id for r in refs]
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            tui._apply_session_selection(app.session, refs, ids)
            app._refresh_scope_view()
            await pilot.pause()
            all_view = _timeline_text(app)
            self.assertIn("multi-session activity", all_view)
            self.assertIn("all 2", all_view)
            self.assertIn("Tab activity", all_view)
            self.assertIn("alpha-result", all_view)
            self.assertIn("beta-result", all_view)

            await pilot.press("tab")
            await pilot.pause()
            first = _timeline_text(app)
            self.assertIn("session 1/2", first)
            self.assertNotEqual("alpha-result" in first, "beta-result" in first)
            first_result = "alpha-result" if "alpha-result" in first else "beta-result"
            first_key = app._timeline_target_key
            other_ref = next(r for r in refs
                             if os.path.abspath(r.path) != os.path.abspath(first_key))
            old_other = tui.S.build(tui.SRC.parse(other_ref.path))
            target = app._watch_target_from_ref(other_ref, st=old_other)
            with open(other_ref.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asst("foreign-update", 0)) + "\n")
            new_other = tui.S.build(tui.SRC.parse(other_ref.path))
            app._on_watch(new_other, tui.S.diff(old_other, new_other), target)
            await pilot.pause()
            self.assertEqual(app._timeline_target_key, first_key)
            self.assertNotIn("foreign-update", _timeline_text(app))

            await pilot.press("tab")
            await pilot.pause()
            second = _timeline_text(app)
            self.assertIn("session 2/2", second)
            self.assertIn("foreign-update", second)
            self.assertNotEqual("alpha-result" in first,
                                "alpha-result" in second)
            self.assertNotEqual("beta-result" in first,
                                "beta-result" in second)

            await pilot.press("tab")
            await pilot.pause()
            back_to_all = _timeline_text(app)
            self.assertIn("all 2", back_to_all)
            self.assertIn(first_result, back_to_all)
            self.assertIn("foreign-update", back_to_all)

            await pilot.press("shift+tab")
            await pilot.pause()
            previous = _timeline_text(app)

        self.assertIn("session 2/2", previous)

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

    async def test_session_hud_summarizes_multi_selection(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        write([user("task b", 100, sessionId="sess-B"), asst("ok", 5)], dir=self.home)
        write([user("task c", 100, sessionId="sess-C"), asst("ok", 5)], dir=self.home)
        refs = sess.sibling_refs()
        ids = [r.session_id for r in refs]
        current = os.path.basename(sess.path)[:-6]
        other = next(sid for sid in ids if sid != current)

        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            tui._apply_session_selection(app.session, refs, [current, other])
            app._refresh_scope_view()
            await pilot.pause()
            hud = str(app.query_one("#session-hud", Static).content)

        self.assertIn("attached sessions", hud)
        self.assertIn("multi-session", hud)
        self.assertIn("2 selected of 3", hud)
        self.assertIn("task", hud)
        self.assertIn("sess-A", hud)
        self.assertTrue("sess-B" in hud or "sess-C" in hud)

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

    async def test_prompt_history_up_down_restores_draft(self):
        sess = self._session("sess-A")
        sess.history = [("user", "first question"), ("assistant", "a1 [L1]"),
                        ("user", "second question"), ("assistant", "a2 [L1]")]
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            comp = app.query_one("#composer", tui.Composer)
            comp.focus()
            comp.insert("draft")
            await pilot.pause()

            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(comp.text, "second question")

            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(comp.text, "first question")

            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(comp.text, "second question")

            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(comp.text, "draft")

    async def test_prompt_history_arrows_do_not_stall_on_slash_commands(self):
        from textual.widgets import OptionList
        sess = self._session("sess-A")
        sess.history = [("user", "before slash"), ("assistant", "a1 [L1]"),
                        ("user", "/watch"), ("assistant", "a2 [L1]"),
                        ("user", "after slash"), ("assistant", "a3 [L1]")]
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            comp = app.query_one("#composer", tui.Composer)
            ol = app.query_one("#slash", OptionList)
            comp.focus()

            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(comp.text, "after slash")

            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(comp.text, "/watch")
            self.assertFalse(app._slash_open)
            self.assertFalse(ol.display)

            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(comp.text, "before slash")

            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(comp.text, "/watch")
            self.assertFalse(app._slash_open)

            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(comp.text, "after slash")

    async def test_prompt_pin_tracks_first_line_and_arrow_jumps(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        sess.history = [
            ("user", "first question"), ("assistant", "a1 [L1]"),
            ("user", "second question\nwith details"), ("assistant", "a2 [L1]"),
            ("user", "third question"), ("assistant", "a3 [L1]"),
        ]
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            comp = app.query_one("#composer", tui.Composer)
            comp.focus()
            pin = app.query_one("#chat-pin", Static)
            self.assertIn("third question", str(pin.content))

            await pilot.press("left")
            await pilot.pause()
            self.assertEqual(app._chat_prompt_nav_index, 1)
            self.assertIn("second question", str(pin.content))
            self.assertNotIn("with details", str(pin.content))  # first line only

            await pilot.press("right")
            await pilot.pause()
            self.assertEqual(app._chat_prompt_nav_index, 2)
            self.assertIn("third question", str(pin.content))

            comp.insert("draft")
            await pilot.pause()
            await pilot.press("left")
            await pilot.pause()
            self.assertEqual(app._chat_prompt_nav_index, 2)      # text cursor only
            self.assertEqual(comp.text, "draft")

    async def test_prompt_pin_click_jumps_to_pinned_prompt(self):
        sess = self._session("sess-A")
        sess.history = [("user", "first question"), ("assistant", "a1 [L1]"),
                        ("user", "second question"), ("assistant", "a2 [L1]")]
        # The 0.35s scroll→pin resync (`set_interval(..., _sync_chat_pin_to_scroll)`)
        # rewrites _chat_pin_index / nav from the scroll position. On a slow runner
        # it fires mid-test, after the drift below, so the click jumps to the wrong
        # index — this is the real cause of the CI 3.13 flake (1 != 0). Neutralize it
        # for this test (which verifies the click→jump, not the resync) by patching
        # the class so the value set_interval captures at mount is the no-op.
        orig_sync = tui.Cockpit._sync_chat_pin_to_scroll
        tui.Cockpit._sync_chat_pin_to_scroll = lambda self: None
        try:
            app = tui.Cockpit(sess, poll=999, alerts=False)
            async with app.run_test() as pilot:
                await pilot.pause()
                # the restored history must be painted before we can pin a prompt:
                # an empty _chat_prompt_widgets() leaves the pin index None and the
                # click a no-op, so settle until it's painted.
                for _ in range(20):
                    if app._chat_prompt_widgets():
                        break
                    await pilot.pause()
                app._update_chat_pin(0)                      # pin the first prompt
                app._chat_prompt_nav_index = 1               # pretend we drifted
                await pilot.click("#chat-pin")
                await pilot.pause()
                self.assertEqual(app._chat_prompt_nav_index, 0)
        finally:
            tui.Cockpit._sync_chat_pin_to_scroll = orig_sync

    async def test_prompt_jump_moves_the_chat_viewport_and_syncs_count(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        sess.history = []
        for i in range(24):
            sess.history.append(("user", f"question {i}"))
            sess.history.append(("assistant", "\n".join(
                f"answer {i}.{j}" for j in range(3)) + " [L1]"))
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            chat = app.query_one("#chat")
            prompts = app._chat_prompt_widgets()
            self.assertEqual(len(prompts), 24)
            bottom_y = chat.scroll_offset.y

            app._jump_chat_prompt(target=3)
            await pilot.pause()
            self.assertLess(chat.scroll_offset.y, bottom_y)
            self.assertEqual(app._chat_prompt_nav_index, 3)
            self.assertEqual(prompts[3].region.y, chat.region.y)
            self.assertIn("4/24", str(app.query_one("#chat-pin", Static).content))

            app._jump_chat_prompt(target=12)
            await pilot.pause()
            self.assertEqual(app._prompt_index_at_chat_top(), 12)
            self.assertEqual(prompts[12].region.y, chat.region.y)

            chat.scroll_end(animate=False)
            await pilot.pause()
            expected = app._prompt_index_at_chat_top()
            app._sync_chat_pin_to_scroll()
            self.assertEqual(app._chat_pin_index, expected)
            self.assertIn(f"{expected + 1}/24",
                          str(app.query_one("#chat-pin", Static).content))

    async def test_prompt_pin_short_chat_keeps_latest_or_selected_prompt(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        sess.history = [("user", "first question"), ("assistant", "a1 [L1]"),
                        ("user", "second question"), ("assistant", "a2 [L1]")]
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(90, 40)) as pilot:
            await pilot.pause()
            chat = app.query_one("#chat")
            self.assertEqual(chat.max_scroll_y, 0)       # content fits; no real top line
            app._sync_chat_pin_to_scroll()
            pin = app.query_one("#chat-pin", Static)
            self.assertEqual(app._chat_pin_index, 1)
            self.assertIn("2/2", str(pin.content))
            self.assertIn("second question", str(pin.content))

            app._jump_chat_prompt(target=0)
            await pilot.pause()
            app._sync_chat_pin_to_scroll()
            self.assertEqual(app._chat_pin_index, 0)
            self.assertIn("1/2", str(pin.content))
            self.assertIn("first question", str(pin.content))

    async def test_prompt_pin_boundary_does_not_switch_one_line_early(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        sess.history = []
        for i in range(18):
            sess.history.append(("user", f"question {i}"))
            sess.history.append(("assistant", "\n".join(
                f"answer {i}.{j}" for j in range(4)) + " [L1]"))
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            chat = app.query_one("#chat")
            prompts = app._chat_prompt_widgets()
            self.assertGreater(chat.max_scroll_y, 0)

            boundary = int(prompts[8].virtual_region.y)
            chat.scroll_to(y=boundary - 1, animate=False, force=True, immediate=True)
            await pilot.pause()
            app._sync_chat_pin_to_scroll()
            pin = app.query_one("#chat-pin", Static)
            self.assertEqual(app._chat_pin_index, 7)
            self.assertIn("8/18", str(pin.content))
            self.assertIn("question 7", str(pin.content))

            chat.scroll_to(y=boundary, animate=False, force=True, immediate=True)
            await pilot.pause()
            app._sync_chat_pin_to_scroll()
            self.assertEqual(app._chat_pin_index, 8)
            self.assertIn("9/18", str(pin.content))
            self.assertIn("question 8", str(pin.content))

    async def test_escape_clears_input_and_double_escape_rewinds(self):
        sess = self._session("sess-A")
        sess.history = [("user", "first question"), ("assistant", "a1 [L1]")]
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            comp = app.query_one("#composer", tui.Composer)
            comp.focus()
            calls = []
            app.action_rewind = lambda: calls.append("rewind")

            comp.insert("draft")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(comp.text, "")
            self.assertEqual(calls, [])

            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(calls, [])

            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(calls, ["rewind"])

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

    async def test_watch_command_marks_dock_and_quotes_vow(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/watch")
            await pilot.pause()
            hud = str(app.query_one("#session-hud", Static).content)
            dock = str(app.query_one("#watch-dock", Static).content)
            chat = "\n".join(str(getattr(s, "content", "") or "")
                             for s in app.query("#chat Static"))

        self.assertTrue(app._watch_mode)
        self.assertNotIn("watch:on", hud)
        self.assertIn("watch", dock)
        self.assertIn("on", dock)
        self.assertIn("Night gathers, and now my watch begins.", chat)

    async def test_watch_stop_clears_hud_marker(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/watch")
            await pilot.pause()
            app._meta("/watch stop")
            await pilot.pause()
            hud = str(app.query_one("#session-hud", Static).content)
            dock = str(app.query_one("#watch-dock", Static).content)

        self.assertFalse(app._watch_mode)
        self.assertNotIn("watch:on", hud)
        self.assertIn("watch", dock)
        self.assertIn("off", dock)

    async def test_watch_stop_prunes_process_updates_from_chat(self):
        from cccopilot import narrate as N
        real_avail = N.available
        N.available = lambda be=None: False
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._chat(app._role(tui.Text("ordinary chat marker"), "role-event"))
                app._meta("/watch")
                await pilot.pause()
                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                st = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st, tui.S.diff(old, st))
                await pilot.pause()
                running = "\n".join(str(w.render()) for w in app.query_one("#chat").children)

                app._meta("/watch stop")
                await pilot.pause()
                stopped = "\n".join(str(w.render()) for w in app.query_one("#chat").children)
        finally:
            N.available = real_avail

        self.assertIn("Night gathers, and now my watch begins.", running)
        self.assertIn("watch · progress", running)
        self.assertIn("ordinary chat marker", stopped)
        self.assertIn("watch · stopped", stopped)
        self.assertIn("/watch view to review", stopped)
        self.assertNotIn("Night gathers, and now my watch begins.", stopped)
        self.assertNotIn("watch · progress", stopped)
        self.assertNotIn("watch · digest", stopped)

    async def test_watch_progress_summary_surfaces_failures(self):
        from cccopilot import narrate as N
        real_avail = N.available
        N.available = lambda be=None: False
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/watch")
                await pilot.pause()
                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                    fh.write(json.dumps(result("t1", "assertion failed", is_error=True, ago=3)) + "\n")
                st = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st, tui.S.diff(old, st))
                await pilot.pause()
                alerts = [w for w in app.query_one("#chat").children
                          if "role-alert" in w.classes]
                rendered = "\n".join(str(w.render()) for w in alerts)
        finally:
            N.available = real_avail

        self.assertIn("watch", rendered)
        self.assertIn("needs attention", rendered)
        self.assertIn("Bash failed", rendered)

    async def test_watch_progress_uses_copilot_narration_when_available(self):
        from cccopilot import narrate as N
        real_avail, real_flow = N.available, N.watch_flow_update
        captured = []
        N.available = lambda be=None: True

        def _flow(flow, model=None, backend=None, instruction=""):
            captured.append((flow, instruction))
            if "watch initial now" in flow:
                return ("now: The watch baseline is captured and the agent is idle [L2].\n"
                        "action: same\n"
                        "title: Baseline\n"
                        "phase: running\n"
                        "reason: watch started\n"
                        "attention: none")
            return ("now: The agent is still running pytest and has not produced a failure yet [L3].\n"
                    "action: new\n"
                    "title: Running tests\n"
                    "phase: testing\n"
                    "reason: pytest started\n"
                    "attention: none")

        N.watch_flow_update = _flow
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/watch")
                for _ in range(10):
                    await pilot.pause()
                    if app._watch_run.last_now_text:
                        break
                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                st = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st, tui.S.diff(old, st))
                rendered = ""
                for _ in range(10):
                    await pilot.pause()
                    rendered = "\n".join(str(w.render())
                                         for w in app.query_one("#chat").children)
                    if "watch · copilot" in rendered:
                        break
        finally:
            N.available = real_avail
            N.watch_flow_update = real_flow

        self.assertTrue(captured)
        event_flow = next(flow for flow, _instruction in captured
                          if "# cc-copilot watch delta" in flow)
        self.assertIn("baseline before watch started", event_flow)
        self.assertIn("previous now", event_flow)
        self.assertIn("in-flight tool", event_flow)
        self.assertIn("watch · copilot", rendered)
        self.assertIn("still running pytest", rendered)

    async def test_watch_accepts_language_preset_like_now_instruction(self):
        from cccopilot import narrate as N
        from textual.widgets import Static
        real_avail, real_flow = N.available, N.watch_flow_update
        captured = []
        N.available = lambda be=None: True

        def _flow(flow, model=None, backend=None, instruction=""):
            captured.append((flow, instruction))
            return ("now: 测试仍在运行，暂时没有失败证据 [L3].\n"
                    "action: same\n"
                    "title: Running tests\n"
                    "phase: testing\n"
                    "reason: pytest started\n"
                    "attention: none")

        N.watch_flow_update = _flow
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/watch 中文")
                await pilot.pause()
                self.assertEqual(app._watch_run.instruction_label, "中文")
                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                st = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st, tui.S.diff(old, st))
                for _ in range(10):
                    await pilot.pause()
                    if captured:
                        break
                app._meta("/watch view")
                await pilot.pause()
                title = str(app.query_one("#watch-monitor-title", Static).content)
                dock = str(app.query_one("#watch-dock", Static).content)
        finally:
            N.available = real_avail
            N.watch_flow_update = real_flow

        self.assertTrue(captured)
        self.assertIn("Answer watch updates in Chinese", captured[0][1])
        self.assertIn("watch instruction: Answer watch updates in Chinese", captured[0][0])
        self.assertIn("中文", title)
        self.assertIn("中文", dock)

    async def test_watch_semantic_step_decision_can_keep_phase_delta_on_same_step(self):
        from cccopilot import narrate as N
        real_avail = N.available
        real_flow = N.watch_flow_update
        N.available = lambda be=None: True

        def _flow_same(flow, model=None, backend=None, instruction=""):
            title = "Watch baseline" if "watch initial now" in flow else "Continuing verification"
            return (f"now: The agent is continuing the same verification thread [L4].\n"
                    "action: same\n"
                    f"title: {title}\n"
                    "phase: testing\n"
                    "reason: same verification run\n"
                    "attention: none")

        N.watch_flow_update = _flow_same
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/watch")
                await pilot.pause()
                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                st = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st, tui.S.diff(old, st))
                for _ in range(10):
                    await pilot.pause()
                    if app._watch_run.last_micro_text:
                        break
        finally:
            N.available = real_avail
            N.watch_flow_update = real_flow

        self.assertEqual(len(app._watch_run.steps), 1)
        self.assertEqual(app._watch_run.steps[0].title, "Continuing verification")
        self.assertIn("continuing the same", app._watch_run.steps[0].summary)

    async def test_watch_semantic_step_decision_can_create_named_step(self):
        from cccopilot import narrate as N
        real_avail = N.available
        real_flow = N.watch_flow_update
        N.available = lambda be=None: True

        def _flow_new(flow, model=None, backend=None, instruction=""):
            if "watch initial now" in flow:
                return ("now: The watch baseline is ready [L2].\n"
                        "action: same\n"
                        "title: Watch baseline\n"
                        "phase: running\n"
                        "reason: watch started\n"
                        "attention: none")
            return ("now: The agent moved into verification with pytest [L4].\n"
                    "action: new\n"
                    "title: Running verification\n"
                    "phase: testing\n"
                    "reason: pytest started after edits\n"
                    "attention: none")

        N.watch_flow_update = _flow_new
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/watch")
                await pilot.pause()
                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                st = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st, tui.S.diff(old, st))
                for _ in range(10):
                    await pilot.pause()
                    if app._watch_run.steps[0].title == "Running verification":
                        break
        finally:
            N.available = real_avail
            N.watch_flow_update = real_flow

        self.assertEqual(len(app._watch_run.steps), 1)
        self.assertEqual(app._watch_run.steps[0].title, "Running verification")
        self.assertEqual(app._watch_run.steps[0].phase, "testing")

    async def test_watch_auto_digest_uses_copilot_step_closure_summary(self):
        from cccopilot import narrate as N
        real_avail = N.available
        real_digest = N.watch_digest_brief
        real_flow = N.watch_flow_update
        captured = []
        N.available = lambda be=None: True

        def _flow(flow, model=None, backend=None, instruction=""):
            if "watch initial now" in flow:
                return ("now: The watch baseline is ready [L2].\n"
                        "action: same\n"
                        "title: Watch baseline\n"
                        "phase: running\n"
                        "reason: watch started\n"
                        "attention: none")
            if "pytest" not in flow:
                return ("now: The agent is editing the implementation [L4].\n"
                        "action: same\n"
                        "title: Editing implementation\n"
                        "phase: editing\n"
                        "reason: same implementation step\n"
                        "attention: none")
            return ("now: The agent moved into verification and pytest is still running [L4].\n"
                    "action: new\n"
                    "title: Running verification\n"
                    "phase: testing\n"
                    "reason: pytest started after implementation\n"
                    "attention: none")

        def _digest(buffer, model=None, backend=None, instruction=""):
            captured.append((buffer, instruction))
            return "The previous watch step closed as verification started with pytest [L4]."

        N.watch_flow_update = _flow
        N.watch_digest_brief = _digest
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/watch")
                for _ in range(10):
                    await pilot.pause()
                    if app._watch_run.last_now_text:
                        break
                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Edit", {"file_path": "app.py"}, "e1", 5)) + "\n")
                    fh.write(json.dumps(result("e1", "ok", ago=4)) + "\n")
                    fh.write(json.dumps(asst("implementation adjusted", ago=3)) + "\n")
                st = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st, tui.S.diff(old, st))
                for _ in range(10):
                    await pilot.pause()
                    if "editing" in app._watch_run.steps[0].phase:
                        break

                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 2)) + "\n")
                st2 = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st2, tui.S.diff(st, st2))
                rendered = ""
                for _ in range(10):
                    await pilot.pause()
                    rendered = "\n".join(str(w.render())
                                         for w in app.query_one("#chat").children)
                    if "watch · digest" in rendered:
                        break
        finally:
            N.available = real_avail
            N.watch_digest_brief = real_digest
            N.watch_flow_update = real_flow

        self.assertTrue(captured)
        self.assertIn("watch digest buffer", captured[0][0])
        self.assertIn("in-flight `Bash`", captured[0][0])
        self.assertIn("step boundary", captured[0][0])
        self.assertIn("watch · digest", rendered)
        self.assertIn("previous watch step closed", rendered)
        self.assertEqual(app._watch_run.events_since_digest, 0)
        self.assertTrue(app._watch_run.digest_buffer)

    async def test_watch_digest_waits_while_copilot_is_busy_then_auto_emits(self):
        from cccopilot import narrate as N
        real_avail = N.available
        real_digest = N.watch_digest_brief
        real_flow = N.watch_flow_update
        captured = []
        N.available = lambda be=None: True
        N.watch_flow_update = lambda flow, model=None, backend=None, instruction="": (
            "now: The watch baseline is ready [L2].\n"
            "action: same\n"
            "title: Watch baseline\n"
            "phase: running\n"
            "reason: watch started\n"
            "attention: none")

        def _digest(buffer, model=None, backend=None, instruction=""):
            captured.append((buffer, instruction))
            return "The queued watch digest now summarizes the buffered test run [L4]."

        N.watch_digest_brief = _digest
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/watch")
                for _ in range(10):
                    await pilot.pause()
                    if app._watch_run.last_now_text:
                        break
                app._watch_run.mode = "quiet"
                app._busy = True
                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                st = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st, tui.S.diff(old, st))
                app._meta("/watch refresh")
                await pilot.pause()
                self.assertFalse(captured)
                self.assertTrue(app._watch_run.pending_digest_reason)

                app._busy = False
                app._tick_refresh()
                rendered = ""
                for _ in range(10):
                    await pilot.pause()
                    rendered = "\n".join(str(w.render())
                                         for w in app.query_one("#chat").children)
                    if "watch · digest" in rendered:
                        break
        finally:
            N.available = real_avail
            N.watch_digest_brief = real_digest
            N.watch_flow_update = real_flow

        self.assertTrue(captured)
        self.assertIn("digest trigger", captured[0][0])
        self.assertIn("watch · digest", rendered)
        self.assertIn("queued watch digest", rendered)

    async def test_watch_view_replaces_chat_but_keeps_activity_timeline(self):
        from textual.widgets import Static
        from cccopilot import narrate as N
        from cccopilot import prefs as PREFS
        real_avail = N.available
        N.available = lambda be=None: False
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/watch")
                await pilot.pause()
                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                st = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st, tui.S.diff(old, st))
                await pilot.pause()
                h0 = app._timeline_height
                app._meta("/watch view")
                await pilot.pause()
                title = str(app.query_one("#watch-monitor-title", Static).content)
                phase = str(app.query_one("#watch-monitor-phase", Static).content)
                digest = str(app.query_one("#watch-monitor-digest", Static).content)
                menu = str(app.query_one("#chat-pin", Static).content)
                self.assertTrue(app._watch_monitor_open)
                self.assertFalse(app.query_one("#chat").display)
                self.assertTrue(app.query_one("#watch-monitor").display)
                self.assertTrue(app.query_one("#timeline").display)
                self.assertNotIn("sess-A", title)
                self.assertNotIn("sess-A", menu)
                self.assertIn("step 1/1", title)
                self.assertIn("latest", title)
                self.assertIn("testing", phase)
                self.assertIn("recent evidence", digest)
                self.assertIn("phase `testing`", digest)
                self.assertNotIn("waiting for enough evidence", digest)

                await pilot.press("left")
                await pilot.pause()
                title_prev = str(app.query_one("#watch-monitor-title", Static).content)
                phase_prev = str(app.query_one("#watch-monitor-phase", Static).content)
                self.assertTrue(app._watch_run.follow_latest)
                self.assertIn("step 1/1", title_prev)
                self.assertIn("latest", title_prev)
                self.assertIn("testing", phase_prev)

                await pilot.press("right")
                await pilot.pause()
                title_latest = str(app.query_one("#watch-monitor-title", Static).content)
                self.assertTrue(app._watch_run.follow_latest)
                self.assertIn("step 1/1", title_latest)
                self.assertIn("latest", title_latest)

                await pilot.press("shift+up")
                await pilot.pause()
                self.assertEqual(app._timeline_height, h0 + 1)
                self.assertEqual(PREFS.get_int("timeline_height", -1), h0 + 1)

                await pilot.press("escape")
                await pilot.pause()
                self.assertFalse(app._watch_monitor_open)
                self.assertTrue(app.query_one("#chat").display)
                self.assertFalse(app.query_one("#watch-monitor").display)
        finally:
            N.available = real_avail

        self.assertIn("watch monitor", title)
        self.assertIn("PHASE", phase)
        self.assertIn("Esc", menu)
        self.assertIn("←/→", menu)

    async def test_watch_command_completion_marks_same_step_done(self):
        from cccopilot import narrate as N
        real_avail = N.available
        N.available = lambda be=None: False
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/watch")
                await pilot.pause()
                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                st1 = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st1, tui.S.diff(old, st1))
                await pilot.pause()

                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(result("t1", "ok", ago=1)) + "\n")
                    fh.write(json.dumps(asst("tests passed", ago=0)) + "\n")
                st2 = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st2, tui.S.diff(st1, st2))
                await pilot.pause()
        finally:
            N.available = real_avail

        self.assertEqual(len(app._watch_run.steps), 1)
        self.assertEqual(app._watch_run.steps[0].status, "done")
        self.assertIn("testing", app._watch_run.steps[0].phase)

    async def test_watch_multi_session_tracks_non_anchor_delta(self):
        from textual.widgets import Static
        from cccopilot import narrate as N
        real_avail = N.available
        N.available = lambda be=None: False
        sess = self._session("sess-A")
        other_path = write([user("task b", 100, sessionId="sess-B"), asst("ok", 5)],
                           dir=self.home)
        sid_a = os.path.basename(sess.path)[:-6]
        sid_b = os.path.basename(other_path)[:-6]
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta(f"/scope multi {sid_a} {sid_b}")
                await pilot.pause()
                app._meta("/watch")
                await pilot.pause()
                self.assertEqual(app._watch_run.target_count, 2)
                target = next(t for t in app._watch_run.targets.values()
                              if os.path.abspath(t.path) == os.path.abspath(other_path))
                anchor_state = app.session.st

                with open(other_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                st = tui.S.build(tui.SRC.parse(other_path))
                app._on_watch(st, tui.S.diff(target.state, st), target)
                await pilot.pause()
                rendered = "\n".join(str(w.render()) for w in app.query_one("#chat").children)
                dock = str(app.query_one("#watch-dock", Static).content)
                app._meta("/watch view")
                await pilot.pause()
                title_b = str(app.query_one("#watch-monitor-title", Static).content)
                phase_b = str(app.query_one("#watch-monitor-phase", Static).content)
                now_b = str(app.query_one("#watch-monitor-now", Static).content)
                menu_b = str(app.query_one("#chat-pin", Static).content)

                app._watch_monitor_target_nav(1)
                await pilot.pause()
                title_a = str(app.query_one("#watch-monitor-title", Static).content)
                phase_a = str(app.query_one("#watch-monitor-phase", Static).content)
                now_a = str(app.query_one("#watch-monitor-now", Static).content)
        finally:
            N.available = real_avail

        self.assertIs(app.session.st, anchor_state)
        self.assertIn("watch · progress", rendered)
        self.assertIn(sid_b[:8], rendered)
        self.assertIn("2 sessions", dock)
        self.assertIn(sid_b[:8], app._watch_run.last_micro_text)
        self.assertIn("session", title_b)
        self.assertIn("task b", title_b)
        self.assertNotIn(sid_b[:8], title_b)
        self.assertIn("testing", phase_b)
        self.assertIn("Bash running: pytest", now_b)
        self.assertIn("Tab", menu_b)
        self.assertNotIn(sid_b[:8], menu_b)
        self.assertNotIn(sid_a[:8], title_a)
        self.assertNotIn(sid_b[:8], title_a)
        self.assertNotIn("pytest", phase_a)
        self.assertNotIn("pytest", now_a)

    async def test_watch_dock_click_starts_then_opens_monitor(self):
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)

        class Click:
            def __init__(self, widget):
                self.widget = widget
                self.stopped = False

            def stop(self):
                self.stopped = True

        async with app.run_test() as pilot:
            await pilot.pause()
            dock = app.query_one("#watch-dock")
            app.on_click(Click(dock))
            await pilot.pause()
            self.assertTrue(app._watch_mode)

            app.on_click(Click(dock))
            await pilot.pause()
            self.assertTrue(app._watch_monitor_open)
            self.assertFalse(app.query_one("#chat").display)
            self.assertTrue(app.query_one("#watch-monitor").display)

    async def test_watch_now_cadence_suppresses_chat_without_auto_digest(self):
        from cccopilot import narrate as N
        real_avail = N.available
        N.available = lambda be=None: False
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._meta("/watch")
                await pilot.pause()
                app._watch_run.micro_interval = 999

                old = app._watch_state
                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest"}, "t1", 4)) + "\n")
                st1 = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st1, tui.S.diff(old, st1))
                await pilot.pause()
                first = "\n".join(str(w.render()) for w in app.query_one("#chat").children)
                first_count = first.count("watch · progress")

                with open(sess.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(tool("Bash", {"command": "pytest -q"}, "t2", 3)) + "\n")
                st2 = tui.S.build(tui.SRC.parse(sess.path))
                app._on_watch(st2, tui.S.diff(st1, st2))
                await pilot.pause()
                second = "\n".join(str(w.render()) for w in app.query_one("#chat").children)
        finally:
            N.available = real_avail

        self.assertGreaterEqual(first_count, 1)
        self.assertEqual(second.count("watch · progress"), first_count)
        self.assertFalse(app._watch_run.last_digest_text)
        self.assertTrue(app._watch_run.digest_buffer)

    async def test_watch_pauses_when_scope_changes(self):
        from textual.widgets import Static
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/watch")
            await pilot.pause()
            b = write([user("other task", 100, sessionId="sess-B"), asst("ok", 5)],
                      dir=self.home)
            app.session.switch_path(b)
            app._watch_scope_changed("attached session changed")
            await pilot.pause()
            hud = str(app.query_one("#session-hud", Static).content)
            dock = str(app.query_one("#watch-dock", Static).content)
            rendered = "\n".join(str(w.render()) for w in app.query_one("#chat").children)

        self.assertTrue(app._watch_mode)
        self.assertTrue(app._watch_run.paused)
        self.assertNotIn("watch:paused", hud)
        self.assertIn("paused", dock)
        self.assertIn("watch · paused", rendered)

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


@unittest.skipUnless(HAVE_TEXTUAL, "textual extra not installed")
class TestWelcomeScreen(unittest.IsolatedAsyncioTestCase):
    """First-run onboarding: pick a model, capture a key, write the config —
    and apply it to the live cockpit without a relaunch."""

    def setUp(self):
        import tempfile
        from cccopilot import onboard as OB  # noqa: F401
        self._saved = {k: os.environ.pop(k, None) for k in
                       ("CC_COPILOT_NO_ONBOARD", "CC_COPILOT_CONFIG",
                        "CC_COPILOT_BACKEND", "OPENAI_API_KEY")}
        self.dir = tempfile.mkdtemp()
        os.environ["CC_COPILOT_CONFIG"] = os.path.join(self.dir, "cc.toml")  # absent → first run

    def tearDown(self):
        for k in ("CC_COPILOT_NO_ONBOARD", "CC_COPILOT_CONFIG",
                  "CC_COPILOT_BACKEND", "OPENAI_API_KEY"):
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    async def _push(self, app):
        from cccopilot import onboard as OB
        result = []
        app.push_screen(tui.WelcomeScreen(OB.detect()), result.append)
        return result

    async def test_api_row_reveals_key_field_and_save_writes_config(self):
        from textual.widgets import RadioSet, RadioButton, Input
        from cccopilot import onboard as OB

        class Host(App):
            def compose(self):
                from textual.widgets import Static
                yield Static("host")

        app = Host()
        async with app.run_test() as pilot:
            result = await self._push(app)
            await pilot.pause()
            scr = app.screen
            # default highlight is a CLI (no key needed) → key field hidden
            self.assertFalse(scr.query_one("#welcome-key", Input).display)
            # choose OpenAI (an API provider)
            buttons = list(scr.query_one("#welcome-choices", RadioSet).query(RadioButton))
            oi = next(i for i, d in enumerate(OB.detect()) if d.choice.name == "openai")
            buttons[oi].value = True
            await pilot.pause()
            keyin = scr.query_one("#welcome-key", Input)
            self.assertTrue(keyin.display)             # API row reveals the key field
            keyin.value = "sk-typed-key"
            scr._save()
            await pilot.pause()
        # config persisted with backend + the typed key, and we won't ask again
        from cccopilot import config as CFG
        data = CFG._load_simple(os.environ["CC_COPILOT_CONFIG"])
        self.assertEqual(data.get("backend"), "openai")
        self.assertEqual(data["env"]["OPENAI_API_KEY"], "sk-typed-key")
        self.assertFalse(OB.needs_onboarding())
        self.assertEqual(result[0][0], "openai")

    async def test_api_row_blocks_save_without_a_key(self):
        from textual.widgets import RadioSet, RadioButton, Input
        from cccopilot import onboard as OB

        class Host(App):
            def compose(self):
                from textual.widgets import Static
                yield Static("host")

        app = Host()
        async with app.run_test() as pilot:
            result = await self._push(app)
            await pilot.pause()
            scr = app.screen
            buttons = list(scr.query_one("#welcome-choices", RadioSet).query(RadioButton))
            di = next(i for i, d in enumerate(OB.detect()) if d.choice.name == "deepseek")
            buttons[di].value = True
            await pilot.pause()
            scr._save()                                 # empty key → must NOT save
            await pilot.pause()
            self.assertEqual(result, [])                # screen still open
        self.assertTrue(OB.needs_onboarding())          # no config written

    async def test_skip_still_writes_config_so_it_stops_asking(self):
        from cccopilot import onboard as OB

        class Host(App):
            def compose(self):
                from textual.widgets import Static
                yield Static("host")

        app = Host()
        async with app.run_test() as pilot:
            result = await self._push(app)
            await pilot.pause()
            app.screen.action_skip()
            await pilot.pause()
        self.assertFalse(OB.needs_onboarding())
        self.assertEqual(result[0], ("skip", None))

    async def test_cockpit_auto_opens_welcome_on_first_run(self):
        from cccopilot.chat import ChatSession
        from cccopilot import narrate as N
        real = N.available
        N.available = lambda b=None: True
        self.addCleanup(lambda: setattr(N, "available", real))
        p = write([user("do it", 90), asst("ok", 30)])
        sess = ChatSession(p)                            # backend=None → eligible
        sess.refresh()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()                          # call_after_refresh lands
            self.assertIsInstance(app.screen, tui.WelcomeScreen)
            # picking a CLI backend applies it to the live session
            app._after_onboard(("claude", tui.OB.choice_for("claude")))
            self.assertEqual(app.backend, "claude")
            self.assertEqual(app.session.backend, "claude")

    async def test_switching_to_cli_via_init_clears_stale_api_model(self):
        from cccopilot.chat import ChatSession
        from cccopilot import narrate as N, onboard as OB
        real = N.available
        N.available = lambda b=None: True
        self.addCleanup(lambda: setattr(N, "available", real))
        p = write([user("x", 30), asst("y", 10)])
        # cockpit launched from an existing API config: model = gpt-4o
        sess = ChatSession(p, backend="openai", model="gpt-4o")
        sess.refresh()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._after_onboard(("claude", OB.choice_for("claude")))   # /init → Claude
            self.assertEqual(app.backend, "claude")
            self.assertIsNone(app.session.model)        # stale gpt-4o dropped
            self.assertIsNone(app.model)


@unittest.skipUnless(HAVE_TEXTUAL, "textual extra not installed")
class TestModelSwitchKeyPrompt(unittest.IsolatedAsyncioTestCase):
    """The quick `/model` (Ctrl+T) switch to an API provider that has no key
    must capture one inline — otherwise it switches silently and the next chat
    fails at call time with "set <PROVIDER>_API_KEY"."""

    def setUp(self):
        import tempfile
        self._saved = {k: os.environ.pop(k, None) for k in
                       ("CC_COPILOT_NO_ONBOARD", "CC_COPILOT_CONFIG",
                        "CC_COPILOT_BACKEND", "CC_COPILOT_MODEL",
                        "DEEPSEEK_API_KEY", "OPENAI_API_KEY")}
        self.dir = tempfile.mkdtemp()
        os.environ["CC_COPILOT_CONFIG"] = os.path.join(self.dir, "cc.toml")
        os.environ["CC_COPILOT_NO_ONBOARD"] = "1"     # don't auto-open WelcomeScreen

    def tearDown(self):
        for k in ("CC_COPILOT_NO_ONBOARD", "CC_COPILOT_CONFIG",
                  "CC_COPILOT_BACKEND", "CC_COPILOT_MODEL",
                  "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def _cockpit(self, backend="codex", model=None):
        from cccopilot.chat import ChatSession
        sess = ChatSession(write([user("x", 30), asst("y", 10)]),
                           backend=backend, model=model)
        sess.refresh()
        return tui.Cockpit(sess, poll=999, alerts=False)

    async def test_switch_to_api_without_key_opens_keyprompt(self):
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_backend("deepseek")              # no DEEPSEEK_API_KEY set
            await pilot.pause()
            self.assertIsInstance(app.screen, tui.KeyPrompt)
            self.assertEqual(app.backend, "codex")    # NOT switched yet — awaiting key

    async def test_keyprompt_save_persists_key_and_switches(self):
        from textual.widgets import Input
        from cccopilot import config as CFG
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_backend("deepseek")
            await pilot.pause()
            scr = app.screen
            scr.query_one("#keyprompt-key", Input).value = "sk-deep"
            scr._save()
            await pilot.pause()
            self.assertEqual(app.backend, "deepseek")
            self.assertEqual(app.session.backend, "deepseek")
            self.assertEqual(app.model, tui.MODELS.default_for("deepseek"))  # provider default applied
        data = CFG._load_simple(os.environ["CC_COPILOT_CONFIG"])
        self.assertEqual(data.get("backend"), "deepseek")
        self.assertEqual(data["env"]["DEEPSEEK_API_KEY"], "sk-deep")
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "sk-deep")  # live this run

    async def test_keyprompt_save_persists_the_requested_model_not_just_default(self):
        # `/model deepseek:deepseek-v4-pro` while the key is captured must save the
        # REQUESTED model, so the next session starts on it (not the provider default).
        from textual.widgets import Input
        from cccopilot import config as CFG
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_backend("deepseek", after_model="deepseek-v4-pro")
            await pilot.pause()
            app.screen.query_one("#keyprompt-key", Input).value = "sk-deep"
            app.screen._save()
            await pilot.pause()
            self.assertEqual(app.model, "deepseek-v4-pro")          # live session
        data = CFG._load_simple(os.environ["CC_COPILOT_CONFIG"])
        self.assertEqual(data.get("backend"), "deepseek")
        self.assertEqual(data.get("model"), "deepseek-v4-pro")      # config matches the live model

    async def test_keyprompt_empty_submit_blocks(self):
        from textual.widgets import Input
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_backend("deepseek")
            await pilot.pause()
            scr = app.screen
            scr.query_one("#keyprompt-key", Input).value = "   "   # whitespace only
            scr._save()
            await pilot.pause()
            self.assertIsInstance(app.screen, tui.KeyPrompt)       # still open
            self.assertEqual(app.backend, "codex")                 # unchanged

    async def test_keyprompt_cancel_keeps_current_backend(self):
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_backend("deepseek")
            await pilot.pause()
            app.screen.action_cancel()
            await pilot.pause()
            self.assertNotIsInstance(app.screen, tui.KeyPrompt)
            self.assertEqual(app.backend, "codex")                 # never switched
        from cccopilot import onboard as OB
        # nothing was persisted by a cancel
        self.assertFalse(os.path.isfile(os.environ["CC_COPILOT_CONFIG"]))

    async def test_switch_to_api_with_key_skips_prompt(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-already"
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_backend("deepseek")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, tui.KeyPrompt)    # no prompt needed
            self.assertEqual(app.backend, "deepseek")
            self.assertEqual(app.model, tui.MODELS.default_for("deepseek"))

    async def test_switch_to_cli_clears_stale_api_model(self):
        os.environ["OPENAI_API_KEY"] = "sk-oi"
        app = self._cockpit(backend="openai", model="gpt-4o")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_backend("claude")                # CLI backend
            await pilot.pause()
            self.assertEqual(app.backend, "claude")
            self.assertIsNone(app.model)              # gpt-4o not passed to claude
            self.assertIsNone(app.session.model)


@unittest.skipUnless(HAVE_TEXTUAL, "textual extra not installed")
class TestCockpitTips(unittest.IsolatedAsyncioTestCase):
    """The slimmed footer's discoverability moved into a rotating tip line."""

    def _session(self):
        from cccopilot.chat import ChatSession
        from cccopilot import narrate as N
        real = N.available
        N.available = lambda b=None: True
        self.addCleanup(lambda: setattr(N, "available", real))
        p = write([user("go", 60), asst("ok", 20)])
        s = ChatSession(p, backend="codex")
        s.refresh()
        return s

    def test_tips_exist_and_fit_a_narrow_cockpit(self):
        self.assertGreaterEqual(len(tui._TIPS), 12)
        for t in tui._TIPS:
            self.assertLessEqual(len(t), 64, t)          # narrow-sidebar contract
            self.assertNotIn("`", t)                     # rendered as plain Text, no md

    def test_resize_keys_hidden_from_footer_essentials_kept(self):
        show = {b.key: b.show for b in tui.Cockpit.BINDINGS}
        for hidden in ("shift+up", "shift+down", "ctrl+r", "ctrl+l"):
            self.assertFalse(show.get(hidden), hidden)   # decluttered (the user's ask)
        for kept in ("ctrl+t", "ctrl+y", "ctrl+c"):
            self.assertTrue(show.get(kept), kept)        # the few high-value keys stay

    def test_next_tip_covers_every_tip_before_repeating(self):
        app = tui.Cockpit(self._session(), poll=999, alerts=False)
        seen = [app._next_tip() for _ in range(len(tui._TIPS))]
        self.assertEqual(set(seen), set(tui._TIPS))      # a full non-repeating pass

    async def test_rotate_tip_renders_a_line(self):
        from textual.widgets import Static
        app = tui.Cockpit(self._session(), poll=999, alerts=False)
        async with app.run_test():
            content = str(app.query_one("#tip", Static).content)
            self.assertIn("💡", content)                  # the subtle marker
            self.assertTrue(any(t in content for t in tui._TIPS))


@unittest.skipUnless(HAVE_TEXTUAL, "textual extra not installed")
class TestModelCatalogSwitch(unittest.IsolatedAsyncioTestCase):
    """The /model surface beyond provider-only: typed model ids, the
    backend:model combo form, and the post-switch model step."""

    def setUp(self):
        import tempfile
        self._saved = {k: os.environ.pop(k, None) for k in
                       ("CC_COPILOT_NO_ONBOARD", "CC_COPILOT_CONFIG",
                        "CC_COPILOT_BACKEND", "CC_COPILOT_MODEL",
                        "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                        "OPENROUTER_API_KEY", "GEMINI_API_KEY")}
        self.dir = tempfile.mkdtemp()
        os.environ["CC_COPILOT_CONFIG"] = os.path.join(self.dir, "cc.toml")
        os.environ["CC_COPILOT_NO_ONBOARD"] = "1"
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"     # keyed → no KeyPrompt detour

    def tearDown(self):
        for k in ("CC_COPILOT_NO_ONBOARD", "CC_COPILOT_CONFIG",
                  "CC_COPILOT_BACKEND", "CC_COPILOT_MODEL", "DEEPSEEK_API_KEY",
                  "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def _cockpit(self, backend="codex", model=None):
        from cccopilot.chat import ChatSession
        sess = ChatSession(write([user("x", 30), asst("y", 10)]),
                           backend=backend, model=model)
        sess.refresh()
        return tui.Cockpit(sess, poll=999, alerts=False)

    async def test_user_switch_offers_to_persist_but_init_does_not(self):
        app = self._cockpit()                          # codex
        async with app.run_test() as pilot:
            await pilot.pause()
            calls = []
            app._offer_persist_default = lambda: calls.append(1)   # stub the worker
            app._set_model("custom-model")             # user model switch → offers
            self.assertEqual(len(calls), 1)
            calls.clear()
            app._set_backend("claude")                 # user backend switch (CLI) → offers
            self.assertEqual(len(calls), 1)
            calls.clear()
            app._after_onboard(("claude", tui.OB.choice_for("claude")))  # /init → no offer
            self.assertEqual(calls, [])

    async def test_offer_writes_config_on_yes(self):
        # The offer only fires when a config already exists (no auto-create); seed
        # one whose default differs from the live backend so the prompt is shown.
        with open(os.environ["CC_COPILOT_CONFIG"], "w") as f:
            f.write('backend = "codex"\n[env]\n[history]\nenabled = true\n')
        app = self._cockpit(backend="deepseek",
                            model=tui.MODELS.default_for("deepseek"))
        async with app.run_test() as pilot:
            await pilot.pause()
            recorded = []

            async def fake_wait(screen):
                return "yes"

            app.push_screen_wait = fake_wait
            real = tui.OB.persist_default
            tui.OB.persist_default = lambda b, m="", path=None: recorded.append((b, m)) or "/x"
            try:
                app._offer_persist_default()
                await app.workers.wait_for_complete()
                await pilot.pause()
            finally:
                tui.OB.persist_default = real
        self.assertEqual(recorded, [("deepseek", tui.MODELS.default_for("deepseek"))])

    async def test_pick_model_cancel_still_offers_to_persist_the_switch(self):
        app = self._cockpit(backend="deepseek",
                            model=tui.MODELS.default_for("deepseek"))
        async with app.run_test() as pilot:
            await pilot.pause()
            calls = []
            app._offer_persist_default = lambda: calls.append(1)

            async def cancel(screen):
                return None                            # Esc the model picker

            app.push_screen_wait = cancel
            app.action_pick_model()
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(len(calls), 1)            # the backend switch is still offered

    async def test_typed_bare_model_switches_model_only(self):
        app = self._cockpit(backend="deepseek",
                            model=tui.MODELS.default_for("deepseek"))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model deepseek-v4-pro")
            await pilot.pause()
            self.assertEqual(app.backend, "deepseek")  # backend unchanged
            self.assertEqual(app.model, "deepseek-v4-pro")
            self.assertEqual(app.session.model, "deepseek-v4-pro")

    async def test_typed_combo_switches_backend_and_model(self):
        app = self._cockpit()                          # starts on codex
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model deepseek:deepseek-v4-pro")
            await pilot.pause()
            self.assertEqual(app.backend, "deepseek")
            self.assertEqual(app.model, "deepseek-v4-pro")

    async def test_typed_backend_name_lands_on_catalog_default(self):
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model deepseek")
            await pilot.pause()
            self.assertEqual(app.backend, "deepseek")
            self.assertEqual(app.model, tui.MODELS.default_for("deepseek"))

    async def test_commit_backend_with_explicit_after_model(self):
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_backend("deepseek", after_model="deepseek-v4-pro")
            await pilot.pause()
            self.assertEqual(app.model, "deepseek-v4-pro")

    async def test_free_form_model_id_accepted(self):
        # the catalog is a convenience, never a restriction
        app = self._cockpit(backend="deepseek")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model some-future-model-id")
            await pilot.pause()
            self.assertEqual(app.model, "some-future-model-id")

    async def test_colonized_model_id_is_not_a_backend_switch(self):
        # OpenRouter variants carry colon suffixes (`…:free`, `…:nitro`) —
        # the colon form only means backend:model when the prefix IS a backend
        app = self._cockpit(backend="deepseek")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model meta-llama/llama-3.1-405b-instruct:free")
            await pilot.pause()
            self.assertEqual(app.backend, "deepseek")  # unchanged
            self.assertEqual(app.model, "meta-llama/llama-3.1-405b-instruct:free")

    async def test_provider_slash_ref_switches_backend_and_model(self):
        os.environ["OPENAI_API_KEY"] = "sk-openai"
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model openai/gpt-5.5")
            await pilot.pause()
            self.assertEqual(app.backend, "openai")
            self.assertEqual(app.model, "gpt-5.5")

    async def test_openrouter_ref_strips_backend_prefix(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or"
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model openrouter/moonshotai/kimi-k2.6")
            await pilot.pause()
            self.assertEqual(app.backend, "openrouter")
            self.assertEqual(app.model, "moonshotai/kimi-k2.6")

    async def test_current_backend_exact_slash_ref_wins(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or"
        app = self._cockpit(backend="openrouter",
                            model=tui.MODELS.default_for("openrouter"))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model anthropic/claude-sonnet-4.6")
            await pilot.pause()
            self.assertEqual(app.backend, "openrouter")
            self.assertEqual(app.model, "anthropic/claude-sonnet-4.6")

    async def test_current_router_backend_keeps_unknown_slash_refs(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or"
        os.environ["OPENAI_API_KEY"] = "sk-openai"
        app = self._cockpit(backend="openrouter",
                            model=tui.MODELS.default_for("openrouter"))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model openai/gpt-6-preview")
            await pilot.pause()
            self.assertEqual(app.backend, "openrouter")
            self.assertEqual(app.model, "openai/gpt-6-preview")

    async def test_google_ref_switches_to_gemini_api(self):
        os.environ["GEMINI_API_KEY"] = "sk-gemini"
        app = self._cockpit()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model google/gemini-3.1-flash-lite")
            await pilot.pause()
            self.assertEqual(app.backend, "gemini-api")
            self.assertEqual(app.model, "gemini-3.1-flash-lite")

    async def test_cli_switch_still_clears_model(self):
        # the stale-model invariant survives the catalog: API → CLI drops it
        app = self._cockpit(backend="deepseek", model="deepseek-v4-pro")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._meta("/model codex")
            await pilot.pause()
            self.assertEqual(app.backend, "codex")
            self.assertIsNone(app.model)


@unittest.skipUnless(HAVE_TEXTUAL, "textual extra not installed")
class TestCockpitStreaming(unittest.IsolatedAsyncioTestCase):
    """Streamed answers paint progressively, finalize once, and never persist
    a partial; exact backend usage replaces the chars/4 estimate in the HUD."""

    def setUp(self):
        import tempfile
        from cccopilot import narrate as N
        self.home = tempfile.mkdtemp(prefix="cctui-stream-")
        self._env = {k: os.environ.get(k) for k in
                     ("CC_COPILOT_STATE_DIR", "CC_COPILOT_HISTORY", "CC_COPILOT_CONFIG")}
        os.environ["CC_COPILOT_STATE_DIR"] = self.home
        os.environ["CC_COPILOT_HISTORY"] = "1"
        os.environ["CC_COPILOT_CONFIG"] = os.path.join(self.home, "none.toml")
        self._realavail = N.available
        self._realstream = N.chat_brief_stream
        N.available = lambda b=None: True

    def tearDown(self):
        from cccopilot import narrate as N
        N.available = self._realavail
        N.chat_brief_stream = self._realstream
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _session(self, sid="sess-A"):
        from cccopilot.chat import ChatSession
        p = write([user("task", 100, sessionId=sid), asst("ok", 50), asst("done", 5)],
                  dir=self.home)
        s = ChatSession(p, backend="codex", alerts=False)
        s.refresh()
        return s

    def _stub_stream(self, chunks, usage=None, fail_after=None):
        """Patch narrate.chat_brief_stream with a StreamHandle over a stub."""
        from cccopilot import narrate as N
        from cccopilot import backends as BK

        class _Stub:
            last_usage = None

        stub = _Stub()

        def gen():
            for i, c in enumerate(chunks):
                if fail_after is not None and i >= fail_after:
                    raise BK.BackendError("stream died")
                yield c
            stub.last_usage = usage

        N.chat_brief_stream = (lambda brief, hist, q, model=None, backend=None:
                               N.StreamHandle(stub, gen()))

    async def test_streamed_answer_finalizes_with_exact_usage(self):
        from cccopilot import backends as BK
        from textual.widgets import Markdown
        self._stub_stream(["streamed ", "answer [L1]"],
                          usage=BK.Usage(100, 42, cost_usd=0.05))
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._on_submit(tui.Composer.Submitted("what happened?"))
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertFalse(app._busy)
            mds = app.query_one("#chat").query(Markdown)
            self.assertEqual(len(mds), 1)
            # the APPENDED chunks must actually land in the widget (append is
            # async under the hood — a silent no-op here means broken streaming)
            self.assertEqual(mds[0].source, "streamed answer [L1]")
        self.assertEqual(sess.history[-1], ("assistant", "streamed answer [L1]"))
        self.assertEqual(app._out_tokens, 42)            # exact, not chars/4
        self.assertTrue(app._out_exact)
        self.assertEqual(app._last_cost, 0.05)
        turns = sess.store._load_turns()
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["usage"]["output_tokens"], 42)

    async def test_chat_turns_carry_timestamp_headers(self):
        from textual.widgets import Markdown
        self._stub_stream(["answer [L1]"])
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._on_submit(tui.Composer.Submitted("what happened?"))
            await app.workers.wait_for_complete()
            await pilot.pause()
            # exactly one user prompt and one copilot header (above the answer)
            self.assertEqual(len(app.query("#chat .role-user")), 1)
            heads = app.query("#chat .turn-head")
            self.assertEqual(len(heads), 1)
            kids = list(app.query_one("#chat").children)
            i = next(k for k, w in enumerate(kids) if "turn-head" in w.classes)
            self.assertIsInstance(kids[i + 1], Markdown)        # header sits on the answer

    async def test_chat_turn_shows_multi_session_evidence_marker(self):
        from cccopilot import scope as SC
        self._stub_stream(["answer [L1]"])
        sess = self._session("sess-A")
        write([user("other task", 100, sessionId="sess-B"), asst("other ok", 50)],
              dir=self.home)
        sess.scope = SC.MULTI
        ids = [r.session_id for r in sess.sibling_refs()
               if os.path.dirname(os.path.abspath(r.path)) == os.path.abspath(self.home)]
        sess.set_scope_sessions(ids)
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._on_submit(tui.Composer.Submitted("compare progress"))
            await app.workers.wait_for_complete()
            await pilot.pause()
            texts = [getattr(getattr(s, "content", None), "plain",
                             str(getattr(s, "content", "")))
                     for s in app.query("#chat .role-event")]
            self.assertTrue(any("evidence · sessions:2" in t for t in texts))
            self.assertEqual(sess.history[-1], ("assistant", "answer [L1]"))

    async def test_clear_racing_answer_done_strands_no_header(self):
        # /clear sets md._pruning synchronously; _answer_done landing in the SAME
        # tick must NOT mount a header before the doomed md — that header would
        # outlive the prune as an orphaned 'copilot HH:MM' with no answer.
        from textual.widgets import Markdown
        self._stub_stream(["half"])
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._answer_chunk(sess.store, "half")        # mount the streaming md
            await pilot.pause()
            self.assertTrue(app._stream_md.is_attached)
            app.action_clear_chat()                       # prune (sets _pruning), no yield
            app._answer_done("q", "half", True, sess.st, sess.store,
                             origin={"hhmm": "09:00"})     # same tick as the clear
            await pilot.pause()                           # let the prune complete
            self.assertEqual(len(app.query("#chat .turn-head")), 0)   # no orphan header
            self.assertEqual(len(app.query_one("#chat").query(Markdown)), 0)
        self.assertEqual(sess.history[-1], ("assistant", "half"))     # still persisted

    async def test_midstream_error_keeps_partial_persists_nothing(self):
        self._stub_stream(["partial text ", "never arrives"], fail_after=1)
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._on_submit(tui.Composer.Submitted("q?"))
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertFalse(app._busy)
            alerts = [w for w in app.query_one("#chat").children
                      if "role-alert" in w.classes]
            self.assertTrue(alerts)
            self.assertIn("not saved", str(alerts[-1].render()))
        self.assertEqual(sess.history, [])               # partial never recorded
        self.assertEqual(sess.store._load_turns(), [])

    async def test_chunks_for_switched_store_buffer_but_do_not_paint(self):
        from textual.widgets import Markdown
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            other_store = object()                       # not the current store
            before = len(app.query_one("#chat").query(Markdown))
            app._answer_chunk(other_store, "ghost chunk")
            await pilot.pause()
            # nothing painted into the conversation the user is looking at…
            self.assertEqual(len(app.query_one("#chat").query(Markdown)), before)
            self.assertIsNone(app._stream_md)
            # …but the answer's own buffer keeps the text (switch-back repaints)
            self.assertEqual(app._stream_buf, "ghost chunk")

    async def test_clear_midstream_remounts_with_accumulated_text(self):
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._answer_chunk(sess.store, "first half ")
            await pilot.pause()
            first_md = app._stream_md
            self.assertTrue(first_md.is_attached)
            app.action_clear_chat()                      # user clears mid-stream
            await pilot.pause()
            self.assertFalse(first_md.is_attached)
            app._answer_chunk(sess.store, "second half")
            await pilot.pause()
            # a NEW widget carries the FULL accumulated text, not just the tail
            self.assertIsNot(app._stream_md, first_md)
            self.assertTrue(app._stream_md.is_attached)
            self.assertEqual(app._stream_buf, "first half second half")

    async def test_switch_away_and_back_still_renders_completed_answer(self):
        # /resume back to the SAME conversation builds a NEW Store object for
        # the same conv_id — the finished answer must render and join history,
        # not vanish until the next reattach (store identity ≠ conv identity)
        from cccopilot import store as ST
        from textual.widgets import Markdown
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            origin = sess.store
            sess.store = ST.Store(origin.conv_id, enabled=True)  # "came back"
            before = len(app.query_one("#chat").query(Markdown))
            app._answer_done("q", "late answer [L1]", True, sess.st, origin)
            await pilot.pause()
            self.assertEqual(sess.history[-1], ("assistant", "late answer [L1]"))
            self.assertEqual(len(app.query_one("#chat").query(Markdown)), before + 1)

    async def test_usage_of_switched_conv_does_not_leak_into_hud(self):
        from cccopilot import backends as BK, store as ST
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            other = ST.Store("conv-elsewhere-0000", enabled=False)
            app._answer_done("q", "a [L1]", True, sess.st, other,
                             BK.Usage(100, 42, cost_usd=0.30))
            await pilot.pause()
            self.assertFalse(app._out_exact)             # turn A's exact usage…
            self.assertIsNone(app._last_cost)            # …never paints conv B's HUD

    async def test_forget_midstream_aborts_and_does_not_resurrect(self):
        from cccopilot import backends as BK
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True                             # mid-answer
            app._answer_store = sess.store               # …for THIS conversation
            app._answer_chunk(sess.store, "doomed partial ")
            await pilot.pause()
            app.action_forget()                          # deletes the conv dir
            await pilot.pause()
            self.assertTrue(app._answer_abandoned)
            from textual.widgets import Markdown
            app._answer_chunk(sess.store, "more")        # late chunk: dropped
            await pilot.pause()
            self.assertEqual(app._stream_buf, "doomed partial ")   # unchanged
            self.assertEqual(len(app.query_one("#chat").query(Markdown)), 0)
            app._answer_done("q", "doomed partial more", True, sess.st,
                             sess.store, BK.Usage(10, 5))
            await pilot.pause()
            self.assertFalse(app._busy)
            self.assertFalse(app._answer_abandoned)      # consumed
        self.assertEqual(sess.history, [])
        # the completed turn must NOT re-create the files /forget removed
        self.assertFalse(os.path.exists(sess.store.turns_path))

    async def test_rewind_midstream_aborts_and_does_not_resurrect(self):
        from cccopilot import backends as BK
        sess = self._session("sess-A")
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            for i in range(2):                           # two committed turns
                app._answer_done(f"q{i}", f"a{i} [L1]", True,
                                 app.session.st, app.session.store)
                await pilot.pause()
            app._busy = True                             # a third answer is mid-stream…
            app._answer_store = sess.store               # …for THIS conversation
            app._answer_chunk(sess.store, "doomed partial ")
            await pilot.pause()
            app._rewind_to(1)                            # fork back before the in-flight turn
            await pilot.pause()
            self.assertTrue(app._answer_abandoned)
            app._answer_done("q2", "doomed answer", True, sess.st, sess.store,
                             BK.Usage(10, 5))            # late completion: dropped
            await pilot.pause()
            self.assertFalse(app._busy)
            self.assertFalse(app._answer_abandoned)      # consumed
        # the fork kept only turn #0; the abandoned turn never reached history/disk
        self.assertEqual(sess.history, [("user", "q0"), ("assistant", "a0 [L1]")])
        self.assertEqual(sess.store.load_history(),
                         [("user", "q0"), ("assistant", "a0 [L1]")])

    async def test_forget_other_conv_leaves_inflight_answer_alone(self):
        # answer running for conversation A; user switches to B and /forgets B —
        # A's unrelated turn must NOT be cancelled or dropped
        from cccopilot import store as ST
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            store_a = sess.store
            app._busy = True
            app._answer_store = store_a                  # in-flight: conv A
            store_b = ST.Store("conv-b-to-forget-000", enabled=True)
            store_b.record_turn("old q", "old a")        # so /forget has files
            sess.store = store_b                         # user is now viewing B
            app.action_forget()
            await pilot.pause()
            self.assertFalse(app._answer_abandoned)      # A's answer untouched
            app._answer_done("q for A", "answer for A", True, sess.st, store_a)
            await pilot.pause()
        self.assertEqual(store_a._load_turns()[-1]["a"], "answer for A")
        self.assertFalse(os.path.exists(store_b.turns_path))   # B stayed deleted

    async def test_chunks_while_hidden_buffer_and_repaint_on_return(self):
        # streaming for conv A; user switches away (chunks arrive unseen) and
        # returns mid-stream — the re-mounted widget must carry ALL the text,
        # not just what streamed while A was visible
        from cccopilot import store as ST
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            origin = sess.store
            app._busy = True
            app._answer_store = origin
            app._answer_chunk(origin, "seen ")
            await pilot.pause()
            sess.store = ST.Store("conv-elsewhere-0001", enabled=False)
            app._rebuild_chat()                          # switch detaches widget
            await pilot.pause()
            app._answer_chunk(origin, "hidden ")         # buffered, not painted
            self.assertEqual(app._stream_buf, "seen hidden ")
            sess.store = origin                          # user comes back
            app._answer_chunk(origin, "back")
            await pilot.pause()
            self.assertEqual(app._stream_md.source, "seen hidden back")

    async def test_message_typed_while_busy_is_queued_not_dropped(self):
        from textual.widgets import Static
        self._stub_stream(["ok [L1]"])
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompts_before = len(app._chat_prompt_widgets())
            app._busy = True                              # simulate a live answer
            app._on_submit(tui.Composer.Submitted("second q"))
            self.assertEqual([m[0] for m in app._msg_queue], ["second q"])  # queued
            # the bubble is DEFERRED until the turn runs, so a queued prompt can
            # never render above the answer it is waiting on (ordering guard)
            self.assertEqual(len(app._chat_prompt_widgets()), prompts_before)
            app._update_status()
            self.assertIn("queued",
                          app.query_one("#status", Static).content.plain)

    async def test_queued_prompt_renders_in_order_when_it_runs(self):
        # deferral means the queued bubble appears only at drain time — i.e. after
        # the current answer, never before it.
        self._stub_stream(["A2 [L1]"])
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            before = len(app._chat_prompt_widgets())
            app._busy = True
            app._on_submit(tui.Composer.Submitted("Q2"))
            self.assertEqual(len(app._chat_prompt_widgets()), before)   # not yet
            app._busy = False
            app._drain_msg_queue()                        # now it runs → bubble mounts
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(len(app._chat_prompt_widgets()), before + 1)

    async def test_queued_message_sends_after_the_current_answer(self):
        self._stub_stream(["answer-two [L1]"])
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._on_submit(tui.Composer.Submitted("queued-q"))
            self.assertEqual([m[0] for m in app._msg_queue], ["queued-q"])
            app._busy = False                             # current answer finished
            app._drain_msg_queue()                        # → the queued one runs
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app._msg_queue, [])          # drained
            self.assertFalse(app._busy)
            self.assertIn(("user", "queued-q"), sess.history)
            self.assertEqual(sess.history[-1], ("assistant", "answer-two [L1]"))

    async def test_queue_is_capped(self):
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            for i in range(tui._MSG_QUEUE_MAX + 3):
                app._on_submit(tui.Composer.Submitted(f"m{i}"))
            self.assertEqual(len(app._msg_queue), tui._MSG_QUEUE_MAX)

    async def test_forget_clears_the_queue(self):
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._on_submit(tui.Composer.Submitted("doomed"))
            self.assertEqual(len(app._msg_queue), 1)
            app.action_forget()
            self.assertEqual(app._msg_queue, [])

    async def test_forget_clears_queue_even_with_history_off(self):
        # --no-persist: action_forget returns early (nothing saved), but it must
        # still discard pending prompts so /forget is consistent.
        from cccopilot.chat import ChatSession
        p = write([user("task", 100, sessionId="sess-np"), asst("done", 5)],
                  dir=self.home)
        sess = ChatSession(p, backend="codex", alerts=False, persist=False)
        sess.refresh()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._msg_queue.append(("doomed", app._evidence_sig(), app.session.store))
            app.action_forget()
            self.assertEqual(app._msg_queue, [])

    async def test_switch_rebuild_clears_the_queue(self):
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._on_submit(tui.Composer.Submitted("for-old-context"))
            self.assertEqual(len(app._msg_queue), 1)
            app._rebuild_chat()                           # a switch/new/rewind
            self.assertEqual(app._msg_queue, [])

    async def test_queued_message_dropped_when_context_changed(self):
        # a switch that does NOT rebuild (e.g. /scope) must not let a queued
        # message be answered against the new evidence — it's dropped.
        from cccopilot import scope as SC
        self._stub_stream(["should-not-run [L1]"])
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._on_submit(tui.Composer.Submitted("for-old-scope"))
            self.assertEqual(len(app._msg_queue), 1)
            sess.scope = SC.MULTI                          # context change, no rebuild
            app._busy = False
            app._drain_msg_queue()
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app._msg_queue, [])           # dropped, not run
            self.assertNotIn(("user", "for-old-scope"), sess.history)

    async def test_ctrl_z_stops_inflight_answer_and_clears_queue(self):
        class _FakeHandle:
            def __init__(s): s.cancelled = False; s.usage = None; s.text = ""
            def cancel(s): s.cancelled = True
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            fake = _FakeHandle()
            app._busy = True
            app._chat_answer_inflight = True
            app._answer_handle = fake
            app._msg_queue.append(("queued", app._evidence_sig(), app.session.store))
            app.action_stop_answer()
            self.assertTrue(fake.cancelled)            # transport killed
            self.assertTrue(app._answer_stopped)       # done-handler will go neutral
            self.assertEqual(app._msg_queue, [])       # decisive stop clears queue

    async def test_stop_before_handle_installed_is_recorded(self):
        # ctrl+z right after submit: the worker may not have published the handle
        # yet, but the turn is still a chat answer and must be honored (P3).
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._chat_answer_inflight = True           # started; handle not set yet
            app._answer_handle = None
            app.action_stop_answer()
            self.assertTrue(app._answer_stopped)        # recorded; worker cancels on install

    async def test_stop_flag_cleared_when_answer_abandoned(self):
        # ctrl+z then /forget (or /rewind) before the cancelled stream lands: the
        # abandoned early-return must still consume the stop flag (P2), or the next
        # normal answer would be wrongly forced through the stopped path.
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._chat_answer_inflight = True
            app._answer_stopped = True
            app._answer_abandoned = True
            app._answer_done("q", "# error: cancelled", False, sess.st, sess.store)
            await pilot.pause()
            self.assertFalse(app._answer_stopped)       # not leaked to the next turn
            self.assertFalse(app._chat_answer_inflight)

    async def test_stop_when_idle_is_a_noop(self):
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_stop_answer()                   # not busy
            self.assertFalse(app._answer_stopped)

    async def test_stop_during_now_since_clears_queue_without_marking_stopped(self):
        # /now and /since are blocking (no _answer_handle) — stop can't cancel the
        # transport, but must NOT set _answer_stopped (it would leak to next turn).
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._chat_answer_inflight = False          # /now·/since, not a chat answer
            app._answer_handle = None
            app._msg_queue.append(("q", app._evidence_sig(), app.session.store))
            app.action_stop_answer()
            self.assertFalse(app._answer_stopped)
            self.assertEqual(app._msg_queue, [])

    async def test_stopped_answer_is_not_persisted(self):
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._answer_stopped = True
            app._busy = True
            app._answer_done("the q", "# error: cancelled", False, sess.st, sess.store)
            await pilot.pause()
            self.assertFalse(app._answer_stopped)      # consumed, no leak
            self.assertFalse(app._busy)
            self.assertNotIn(("user", "the q"), sess.history)   # not persisted
            self.assertEqual(app.query_one("#composer", tui.Composer).text, "the q")
            texts = []
            for s in app.query("#chat Static"):
                c = getattr(s, "content", None)
                texts.append(getattr(c, "plain", str(c)))
            self.assertFalse(any("stopped" in t for t in texts))  # no history row

    async def test_ctrl_z_removes_live_turn_and_restores_prompt(self):
        class _FakeHandle:
            def __init__(s): s.cancelled = False
            def cancel(s): s.cancelled = True

        from textual.widgets import Markdown
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app._prompt_widget("edit this question")
            app._chat(prompt)
            scope = app._make_answer_scope_widget()
            app._chat(scope)
            app._answer_prompt_widget = prompt
            app._answer_scope_marker_widget = scope
            app._answer_prompt_text = "edit this question"
            app._answer_prompt_history_added = True
            app._prompt_history.append("edit this question")
            app._busy = True
            app._chat_answer_inflight = True
            app._answer_store = sess.store
            app._answer_handle = _FakeHandle()
            app._answer_chunk(sess.store, "partial answer")
            await pilot.pause()
            self.assertEqual(len(app.query("#chat .role-user")), 1)
            self.assertEqual(len(app.query_one("#chat").query(Markdown)), 1)

            app.action_stop_answer()
            await pilot.pause()

            self.assertTrue(app._answer_handle.cancelled)
            self.assertEqual(app.query_one("#composer", tui.Composer).text,
                             "edit this question")
            self.assertEqual(len(app.query("#chat .role-user")), 0)
            texts = [getattr(getattr(s, "content", None), "plain",
                             str(getattr(s, "content", "")))
                     for s in app.query("#chat .role-event")]
            self.assertFalse(any("evidence ·" in t for t in texts))
            self.assertEqual(len(app.query_one("#chat").query(Markdown)), 0)
            self.assertEqual(app._prompt_history, [])

    async def test_stop_meta_command_routes_to_action(self):
        from unittest import mock
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            with mock.patch.object(app, "action_stop_answer") as spy:
                app._meta("/stop")
                app._meta("/cancel")
            self.assertEqual(spy.call_count, 2)         # both aliases route to stop

    async def test_prompt_queued_during_abandon_window_drains(self):
        # /forget cancels the answer but _busy stays true until the worker lands;
        # a prompt queued in that window must still drain (not sit idle forever).
        self._stub_stream(["A [L1]"])
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._chat_answer_inflight = True
            app._answer_abandoned = True
            app._msg_queue.append(("after-forget", app._evidence_sig(),
                                   app.session.store))
            app._answer_done("orig", "# error: cancelled", False, sess.st, sess.store)
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app._msg_queue, [])               # drained, not stuck
            self.assertIn(("user", "after-forget"), sess.history)

    async def test_message_queued_during_now_drains_when_now_finishes(self):
        # /now sets _busy but finishes in _now_done (not _answer_done); a message
        # queued meanwhile must still drain there, in FIFO order.
        self._stub_stream(["after-now [L1]"])
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True                              # simulate /now in flight
            app._on_submit(tui.Composer.Submitted("ask-during-now"))
            self.assertEqual([m[0] for m in app._msg_queue], ["ask-during-now"])
            app._now_done("/now", "next step [L1]",
                          (app._evidence_sig(), app.session.store))
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app._msg_queue, [])
            self.assertIn(("user", "ask-during-now"), sess.history)

    async def test_message_queued_during_since_drains_when_since_finishes(self):
        self._stub_stream(["after-since [L1]"])
        sess = self._session()
        app = tui.Cockpit(sess, poll=999, alerts=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True                              # simulate /since in flight
            app._on_submit(tui.Composer.Submitted("ask-during-since"))
            self.assertEqual([m[0] for m in app._msg_queue], ["ask-during-since"])
            app._since_done("/since", "recap [L1]",
                            (app._evidence_sig(), app.session.store), lambda: None)
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app._msg_queue, [])
            self.assertIn(("user", "ask-during-since"), sess.history)


if __name__ == "__main__":
    unittest.main()
