# Changelog

## Unreleased

## 0.13.0 — 2026-06-09

**The status bar reflows to width — a narrow sidebar keeps every field.** Before,
the bottom status bar was a single `height: 1` line that got cropped on the right
when the window was narrow, so in a sidebar the verdict, watched session, idle
counters, and context HUD simply disappeared. It now adapts:
- **Wide** (fits): unchanged single dense line.
- **Medium**: an identity row (status · verdict · copilot · watching) + an
  activity/HUD row.
- **Narrow** (sidebar): author-controlled stacked rows — status + verdict pinned
  to row 1, then copilot, `↳ <session> · idle`, and the context HUD split onto its
  own rows. Nothing is dropped; `watching` abbreviates to `↳`. At brutal widths
  the verdict badge demotes to its own row rather than clip.

The layout is **measurement-driven** (it picks the widest layout whose content
actually fits `self.size.width`, re-rendered on resize), and the HUD rows are
**split from `format_hud`/`format_answering`'s own output**, so the CLI formatters
stay the single source of truth and a future HUD field flows in for free. Growth
is bounded (`max-height: 8`) so the HUD can't starve the chat pane. A regression
test asserts every datum on the wide line survives into the narrow stack.

## 0.12.1 — 2026-06-09

**Flat (30,30,30) ground — the neutral background is now what you actually see.**
v0.12.0 set `background` to `#1e1e1e`, but the main panes (header / timeline /
chat) paint with `$panel`, which was a lighter `#2d2d2d` (45,45,45) — so a
low-contrast layer floated over the asked-for color and that lighter grey is what
filled the screen. `$panel` now equals `background`, so the panes sit flush on
the (30,30,30) ground and separation comes from the borders. A test pins
`panel == background` so the layer can't creep back.

## 0.12.0 — 2026-06-09

**A neutral cockpit, and each agent shown in its own color.** The default theme
gets a quieter ground and a more meaningful identity:
- **Neutral graphite ground.** The cockpit background moves off the old blue-ink
  ramp to a neutral grey (`#1e1e1e` and a grey `surface`/`panel`/`boost` ramp),
  so it reads as a calm workspace rather than a tinted block.
- **The accent IS the Claude×Codex blend.** The copilot's own accent (borders,
  the timeline title, its own chat replies) is now `#807ea6` — literally the
  midpoint of Claude's `#cb7d5b` and Codex's `#347ff2`. The copilot's color is
  the average of the two agents it watches. (A test pins the accent to that
  computed midpoint, so it can't silently drift.)
- **Per-agent brand colors.** Agent-identity spans now carry the *watched*
  agent's brand hue: a Claude session's `agent` label and `"<agent> session"`
  header glow rust, a Codex session's blue. In a multi-session view, each row
  takes its own session's color, so a mixed Claude+Codex timeline is legible at
  a glance. Unknown agents fall back to the copilot accent.

## 0.11.2 — 2026-06-09

**The chat and timeline panes now line up.** Two theme/layout nits:
- **Scrollbars match and align.** The chat used a thicker (2-cell) scrollbar that
  floated a couple columns short of the screen edge, while the timeline's was a
  thin 1-cell bar at the edge. The chat now uses a 1-cell bar at the same column
  (`VerticalScroll`'s default `width: 1fr` was reserving gutter and leaving the
  pane 2 columns narrow — pinned to `width: 100%`; both panes drop their right
  padding so the bars reach the edge).
- **One continuous surface.** The chat was `$surface` while the timeline/header
  are `$panel`, so it read as a separate color block; it's now `$panel` too, so
  the panes blend.

## 0.11.1 — 2026-06-09

**The chat gutter bars now match the timeline's.** The activity timeline draws a
left half-block `▌` gutter, but the chat message blocks (and the `/since` etc.
collapsibles) used a heavier full-block border that read as too wide. They now
use the same `▌` glyph (`border-left: outer`), so the two panes line up.

## 0.11.0 — 2026-06-09

**`/since` is now a grounded LLM recap.** Re-entering after the agent worked, you
get a short natural-language recap of what changed — *narrated by the model from
the deterministic, `[L…]`-cited delta*, not free-associated. It stays true to
cc-copilot's non-hallucinating identity: the model sees only the cited delta (the
same evidence `/since --raw` prints) and keeps its citations.

- **Recap on top, cited evidence beneath** — read the narrative for speed, drop
  to the `[L…]` lines to verify.
- **Recap by default when a backend is available; deterministic fallback when
  not** — with `--no-backend`, or `/since --raw`, you get the instant cited delta
  and no model call. The model is also skipped when nothing changed.
- In the cockpit, `/since` runs the narration on a worker thread (spinner, no UI
  freeze); the re-entry "N new since you last looked" banner stays instant.
- CLI: `cc-copilot since [when] [--raw] [--model …] [--backend …]`.

## 0.10.5 — 2026-06-09

**Timeline horizontal panning + a scroll-position fix.**
- **Long lines pan sideways with no scrollbar.** Activity lines no longer wrap;
  a long line (a deep path, a long error) stays on one row and you pan across it
  with the trackpad / shift-wheel. The horizontal scrollbar is drawn at
  zero thickness, so there's no chunky bar eating a row — scrolling without the
  gutter. (The vertical history scrollbar stays a thin 1 cell.) Timeline rows are
  also kept much longer (up to 200 chars, was ~58) so there's real content to pan
  to. The pan survives same-session refreshes — it no longer snaps back to
  column 0 on every poll / theme change / `/refresh` while you're tailing.
- **Scroll position follows the evidence, not the clock.** The 0.10.4
  scroll-preserve was applied to *every* rebuild, so switching evidence
  (`/sessions`, `/use`, `/here`, `/resume`) restored the previous view's offset —
  opening a freshly-selected session scrolled into the middle. Whether to keep
  the scroll is now *derived* from whether the evidence identity (scope · session ·
  multi-session set) actually changed: same-evidence rebuilds (poll tick, theme,
  `/refresh`, re-observe, a no-op `/scope`) hold your position; an evidence switch
  lands on the newest line. (All three fixes above caught by Codex cross-model
  review.)

## 0.10.4 — 2026-06-08

**Activity-timeline review fixes.** A multi-pass self-review of the 0.10.3
RichLog timeline surfaced several rough edges, now fixed:
- **Scroll-up no longer gets yanked to the bottom.** A full rebuild (an
  `--no-alerts` growth tick, or any project / multi-session scope, which rebuilds
  on every poll) snapped unconditionally to the newest line — defeating the
  tail-follow guarantee. It now preserves your scroll position, following only
  when you were *exactly* at the bottom (so a reader one line up isn't yanked
  either).
- **The gutter bar no longer bleeds its color into the line.** The `▌` prefix
  color was the base style of the whole line, tinting the status and file-change
  text accent-purple; it is now scoped to just the glyph.
- **Warn vs. alarm gutter color.** Soft "warn" observer lines get an amber bar
  that matches their text; only true failures / alarms are red.
- **No more spurious horizontal scrollbar** in the timeline — long lines wrap to
  the panel width (`min_width=1` + `overflow-x: hidden`) instead of forcing a
  sideways scroll.
- Narrower exception handling on the title update, and dead-code cleanup
  (gutter fallback branch, an unused test import).

## 0.10.3 — 2026-06-08

**The activity timeline now holds the *entire* session — scroll through all of
it, no cap.** The previous limit (~150 events) existed because the panel mounted
one widget per line, which is O(N) (3000 lines ≈ 5 s). The timeline is now a
`RichLog`, which keeps all lines but only renders the visible window — so the
whole history is scrollable and a 1500-event session loads in ~0.3 s. Tail-follow
(scroll-up sticks), the colored gutter bars, and the pinned "session activity"
title are all preserved.

## 0.10.2 — 2026-06-08

**Scrollable activity history.** The cockpit's activity timeline seeded only the
last 5 events and snapped back to the bottom on every update, so there was almost
nothing to scroll and reviewing was impossible. Now it:
- **seeds ~150 recent events** so there's real history to scroll back through
  (scroll with the mouse wheel — the panel shows a scrollbar when it overflows);
- **tail-follows** — only auto-scrolls to the newest line when you're already at
  the bottom, so scrolling up to read isn't yanked back down by the next event;
- **clamps** a persisted timeline height to fit the current terminal (a tall
  height saved on a big screen won't crowd out the chat on a small one).

## 0.10.1 — 2026-06-08

**Timeline resize keys are now macOS-safe.** 0.10.0 used `Ctrl+↑/↓`, which macOS
grabs for Mission Control (and `Ctrl+[` is literally Escape, so a `[`/`]` pair
can't be bound). The primary resize keys are now **`Shift+↑` / `Shift+↓`**;
`Ctrl+↑/↓` stay as a hidden alias for platforms where they get through.

## 0.10.0 — 2026-06-08

**Resizable activity timeline.** The cockpit's agent-activity strip was fixed at
6 rows. Resize the timeline/chat split live with **Ctrl+↑ / Ctrl+↓** (the chat
pane fills the rest), and the height is **remembered across launches** — stored
in `ui.json` under the state home, or pin a default with
`CC_COPILOT_TIMELINE_HEIGHT`. Bounds 3–24 rows. Shown in the footer and `/help`.

## 0.9.5 — 2026-06-08

**Your live session sorts to the top of `/sessions`.** The picker was ordered by
pure recency (mtime, newest first), and the cross-project live session was
appended to the *bottom* — so the session you most often want was buried. The
cockpit/REPL `/sessions` picker now lists your own current (live) session first,
then everything else newest-first (still agent-agnostic — Claude and Codex
interleaved by recency). The status board and the evidence path are unchanged.

## 0.9.4 — 2026-06-08

**Your custom session name wins over the auto-title.** A session you renamed
(e.g. "dev") was showing its Claude-generated title ("Design CC Copilot…")
instead. cc-copilot treated `ai-title` and `custom-title` with equal precedence
(latest-by-position wins), and Claude Code keeps re-emitting the `ai-title` as
the conversation grows, so the auto-title landed after your rename and overrode
it. Now a human-set name (`custom-title`, or the session `name`) always beats the
auto-generated `ai-title`, in both `/sessions` and brief headers.

## 0.9.3 — 2026-06-08

**Readable selection highlight.** The cursor row in the `/sessions` and other
pickers was nearly invisible — the default highlight derives from the theme's
near-identical dark shades, and is dimmer still when the list isn't focused (the
filter has focus). The highlighted option now uses a distinct, theme-derived
band (`$secondary` blue tint) + bold, applied regardless of focus, so it's
obvious which row the cursor is on in every theme. Hover gets a lighter band too.

## 0.9.2 — 2026-06-08

**Clearer multi-session picker.** The `/sessions` checkbox picker was confusing:
with no on-screen hint, pressing Enter (the natural "select") confirmed and closed
it, so the `[ ]` / `[x]` boxes seemed to "disappear" and multi-select felt broken.
- Added a live **"(N selected)"** count in the title and a persistent key hint:
  *Space / click toggle · Enter confirm · Esc cancel · type to filter*.
- Toggled rows now render a **bold green `[x]`** and bold label that stay legible
  under the highlight bar (the mark no longer washes out when a row is selected).
- Toggling via space **or** click both update the count immediately.

## 0.9.1 — 2026-06-08

**Observe your own current session.**
- **Fixed current-session detection.** Claude Code renamed the session env var to
  `CLAUDE_CODE_SESSION_ID`, but cc-copilot still read the old `CLAUDE_SESSION_ID`,
  so it could no longer tell which session is *yours*. Both names now work
  (`resolve` and the pickers).
- **`/sessions` always includes your current (live) session** — even when it's in
  a different project than the one the cockpit is watching — marked "⟵ your live
  session". Fixes "`/sessions` cannot show our current session": cc-copilot is
  project-scoped, so a session you're sitting in elsewhere would never appear.
- New **`--here`** flag and **`/here`** command (cockpit + REPL) to observe the
  session you're running inside of directly, regardless of cwd.

## 0.9.0 — 2026-06-08

**"While you were away" — the re-entry layer.** Built on the normalized model, so
it works for Claude Code and Codex sessions alike.

- **`since`** — a deterministic, evidence-cited "what changed since you last
  looked": your unanswered asks, the agent's new messages, commands run,
  failures, files changed, and any status/safety transition — every line citing
  a `[L<n>]`. `cc-copilot since` (since your last look) or `cc-copilot since 30m`
  / `2h` / `1d` for a time window; REPL/cockpit `/since`. A small per-session
  last-look marker is stored under `$CC_COPILOT_STATE_DIR` (never `~/.claude` or
  `~/.codex`); the cockpit stamps it on exit and greets you with "⟳ N new since
  you last looked" on return. `--peek` shows without advancing the marker.
- **`handoff`** — a shareable Markdown artifact bundling the brief + an optional
  "while you were away" section + metadata, citations preserved.
  `cc-copilot handoff [--out FILE]`; REPL/cockpit `/handoff [file]`.
- **`watch --notify`** — conservative away-alerts: a desktop notification
  (macOS/Linux, terminal-bell fallback) only on the *transition into* needing
  you — a fresh `intervene` verdict, a slide into `stalled`, or a new failure —
  never steady-state noise.
- Codex tool output is de-wrapped for display (the `Chunk ID` / `Wall time` /
  `Process exited` / `Output:` envelope) so briefs and `since` read clean; the
  pass/fail status is still parsed from the full wrapper.
- 36 new tests (since / lastlook / handoff / notify / CLI + codex output). Full
  suite green under a clean-runner and `LC_ALL=C`.

## 0.8.0 — 2026-06-08

**Cross-model agent adapters** — cc-copilot is no longer Claude-only.
- New `sources/` adapter layer with an `AgentSource` contract (discover sessions
  + parse a transcript) and a path/cwd dispatcher. Claude Code is now adapter #1
  (a behavior-preserving delegation to the existing parser/locator); **Codex is
  adapter #2**, reading `${CODEX_HOME:-~/.codex}/sessions/**/rollout-*.jsonl`.
- One cockpit watches **Claude Code and Codex sessions side by side**, grouped by
  project cwd and tagged by agent. `sessions` and `status` show an agent column;
  `/sessions`, multi-session scope, and the cockpit picker span both agents.
- The Codex adapter normalizes Codex's tool vocabulary into the canonical model
  so the existing deterministic state folding works unchanged: `exec_command` /
  `shell` → `Bash` (with exit-code → failure), `apply_patch` → per-file
  `Edit`/`Write`, and `update_plan` → `TodoWrite` (the plan surfaces in recaps).
  `reasoning` maps to agent thinking; the duplicate `event_msg` stream is ignored
  so messages aren't double-counted.
- Scope discovery: `--agent claude|codex` (repeatable), `$CC_COPILOT_AGENTS`, or
  `[agents] enabled = [...]` in `~/.cc-copilot.toml`. Default: every agent whose
  storage exists. Sources whose home dir is absent are silently skipped.
- Read-only contract now spans agents: never writes under `~/.claude` **or**
  `~/.codex`. cc-copilot's own `codex exec` narration sessions are detected and
  hidden, same as for Claude.
- See [docs/cross-model-adapters.md](docs/cross-model-adapters.md). 29 new tests
  (Codex parse→state, dispatcher routing/union/gating, cross-agent discovery).

## 0.7.0 — 2026-06-08

**Evidence context engine**
- Added the first v0.7 evidence-expansion path for model-backed `ask`, `chat`,
  and cockpit answers: raw transcript records are retrieved by recent tail,
  cited line, and question keyword before summaries are used as orientation.
- Tool call/result pairs are kept together in retrieved evidence, with
  session-qualified `[session:L…]` citations for raw records.
- Cockpit conversation replay is now budgeted by context size instead of a
  fixed `history[-8:]` turn window.
- Added local context-usage estimates and a cockpit HUD segment showing input
  context, output estimate, raw/project/chat/memory/index split, and model
  window budget.
- Added budget-triggered durable cockpit memory: older Q&A compacts into a
  deterministic `memory.json` sidecar while the complete raw `turns.jsonl` log
  remains intact.
- Made model-facing project context tiered and question-aware: git summary,
  changed/key files, relevant excerpts, and broader file index now fit a bounded
  project budget instead of always sending the same project packet.

**Cockpit answer quality**
- Reframed LLM-backed `ask`, `chat`, and narration prompts around cited
  evidence context instead of a "brief", so the cockpit agent is less likely to
  answer by describing its own packet and more likely to synthesize grounded
  next steps.
- Kept legacy self-session detection while hiding new backend narration calls
  from project session lists.

**Claude config directory**
- Session discovery now honors `$CLAUDE_CONFIG_DIR`, while keeping `~/.claude`
  as the fallback, so cockpit/status work with isolated Claude config roots.

**Cockpit keyboard cleanup**
- Deprecated `Ctrl+S`, `Ctrl+O`, and `Ctrl+H` cockpit shortcuts. Use slash
  commands or the command palette instead.
- The TUI `/sessions` picker now supports checkbox multi-select (`[ ]` /
  `[x]`): one checked session means single-session evidence, multiple checked
  sessions mean multi-session evidence. Project context stays on rather than
  being a separate picker mode.
- Pressing Enter on an open slash-command suggestion now accepts the highlighted
  command; Tab completion is optional.

**Cockpit theme polish**
- Replaced Textual's generic theme picker with a curated cockpit palette
  switcher: `cockpit`, `graphite`, `signal`, and `daybreak`.
- Added `/theme` and a `Cockpit Theme` command palette action.
- Added `CC_COPILOT_THEME` for selecting the startup palette.

## 0.6.0 — 2026-06-07

**Cockpit Sessions**
- Promoted persisted chat state into resumable **Cockpit Sessions**: Q&A,
  backend/model, project cwd, evidence range, and selected evidence sessions now
  resume together.
- Changing the agent evidence session no longer swaps to another chat log; it
  keeps the current Cockpit Session and updates the saved evidence target.
- Added `/resume` and `cc-copilot resume`; `/history` and `cc-copilot history`
  remain backward-compatible aliases.
- Added `/new` to start an independent Cockpit Session over the current project
  and evidence target.

**Always-on project context**
- Conversational Q&A surfaces now include bounded read-only project context by
  default, even when the agent evidence range is a single session.
- The cockpit header and status language now distinguish Cockpit Session,
  project context, and agent-session evidence instead of leaking the old
  "attached session + scope" model.

## 0.5.1 — 2026-06-07

**Cockpit hotfix**
- Fixed a Textual crash when opening `multi-session · select sessions` or
  `project · select sessions` from the scope picker.
- Renamed the multi-picker's internal option rebuild helper so it no longer
  collides with Textual's widget rendering internals.
- Added regression coverage for keyboard-only multi-session selection.

## 0.5.0 — 2026-06-07

**Agent observability core**
- Added `cc-copilot observe`, a deterministic, evidence-cited operator report
  for the selected read scope.
- The observer report renders a ranked **Now** board, **Attention Queue**,
  **Next Human Decision**, and **Recent Evidence** without calling an LLM.
- `observe` supports `session`, `multi-session`, and `project` scopes, including
  `--scope-sessions` subsets and session-qualified citations like
  `[b5c53c29:L244]`.
- Project-scope observation includes a compact git glance with `[git:*]`
  citations while preserving the read-only contract.

**Cockpit attention surface**
- Added `/observe` to the REPL and cockpit as an LLM-free command.
- Added `/observe` to cockpit slash autocomplete and the command palette.
- The cockpit activity strip now shows a live attention line derived from the
  observer core, so the UI names the smallest next human decision instead of
  only listing recent transcript activity.

## 0.4.0 — 2026-06-07

**Grounding scopes**
- Added explicit read ranges for conversational surfaces: `session` (default),
  `multi-session` / `multi`, and `project` / `repo`.
- `brief`, `check`, `ask`, `chat`, and `cockpit` accept `--scope`; the REPL and
  cockpit also support `/scope`, and the cockpit exposes a `Ctrl+O` scope picker.
- Multi-session/project scopes can be narrowed to a specific subset with
  `--scope-sessions a1b2c3d4,b5c53c29`, `/scope multi a1b2c3d4 b5c53c29`, or
  the cockpit picker; `/scope all` clears the subset.
- Multi-session scope renders all work-session transcripts for the cwd as a
  deterministic evidence brief, neediest-first, with session-qualified citations
  like `[b5c53c29:L244]`.
- Project scope adds deterministic read-only workspace facts: git status, a
  bounded file index, and text excerpts with `[path:Ln]`, `[tree]`, and
  `[git:*]` citations. The backend still receives only a rendered brief: no
  tools, no ambient repo access, no writes.

**Cockpit scope surfaces**
- The TUI now has a dedicated status header that changes by scope: single
  session shows the attached title/id/status, multi-session shows selected/all
  session health, and project scope adds git/project status.
- The activity strip now retitles and repopulates by scope (`session activity`,
  `multi-session activity`, `project activity`) instead of always implying a
  single observed session.
- Scoped cockpit surfaces refresh on the poll interval, so the header/activity
  view stays current without relying on manual `Ctrl+R` refresh.
- The chat/cockpit poll default is now 2 seconds, with `--poll N` still available
  for slower polling on very large session sets.

## 0.3.0 — 2026-06-07

**Rewind** (Codex-style, conversation-only)
- Fork the copilot chat from an earlier message: **Esc on an empty composer** (or
  `/rewind`, the palette, `Ctrl+P`) opens a picker of your prior questions; choosing
  one discards it and everything after, truncates the saved history to match, and
  reloads that message into the composer to edit and re-ask. REPL: `/rewind` lists,
  `/rewind <n>` forks. Because cc-copilot is read-only on the agent, rewind only
  affects *your* conversation — never the observed agent's code (the same scope as
  Codex's Esc-rewind). Backed by `Store.truncate(n)` (atomic, under the lock).

**Cockpit fixes from real-world testing**
- **Multi-character CJK / IME input** no longer garbles. The Kitty keyboard
  protocol's "associated text" feature encoded pinyin-committed text as
  colon-separated codepoints that Textual mis-parsed, leaking raw escapes like
  `[49;;29616:22312u`. Kitty is now disabled by default so IME input arrives as
  plain UTF-8 (re-enable with `TEXTUAL_DISABLE_KITTY_KEY=0`; newline is then
  `Ctrl+J`, since `Shift+Enter` needs Kitty disambiguation).
- **`/` command autocomplete**: typing `/` lists matching commands above the
  composer (↑/↓ to move, Tab to complete, Esc to dismiss).
- **Clear vs forget, disambiguated**: `Ctrl+L` ("clear view") only tidies the
  screen and keeps saved history; new **`/forget`** deletes *this* conversation's
  saved history. The Sessions picker (`Ctrl+S` — "observe a live agent session")
  and History picker (`Ctrl+H` — "reopen a saved copilot conversation") are
  relabeled so their distinct roles are obvious.

## 0.2.0 — 2026-06-07

**Persistent copilot history** — your Q&A with the copilot now survives.
- Each conversation is keyed to the observed Claude Code session and stored locally
  (append-only JSONL + a derived meta cache) under `$CC_COPILOT_STATE_DIR` >
  `$XDG_STATE_HOME/cc-copilot` > `~/.local/state/cc-copilot`. Never under `~/.claude`.
- Switching sessions in the cockpit (or `/use`, or relaunching) now **restores** that
  session's prior dialogue instead of wiping it — the reported data loss.
- `/history` (Ctrl+H) browses & restores past conversations; `/history this|all` in the
  REPL; standalone `cc-copilot history [--all]`. Works even if the transcript is gone
  (read-only history-only view).
- Concurrency-safe: a single `fcntl.flock(LOCK_EX)` guards the whole append+meta write and
  the turn count is re-derived under the lock (two cockpits on one session can't corrupt or
  drift it). No silent unsafe fallback. All writes best-effort — a storage error never
  breaks an answer or the read-only contract. Dirs 0700 / files 0600.
- Opt out with `--no-persist`, `[history] enabled = false`, or `CC_COPILOT_HISTORY=0`.

**Cockpit input**
- Click anywhere in the cockpit to focus the composer (the timeline/chat panes no longer
  steal focus) — fixes IME / multilingual input having no target.
- Full multilingual (CJK / emoji / accented) input; **Shift+Enter / Ctrl+J** now actually
  insert a newline (previously advertised but inert).

**UTF-8 everywhere** — CLI-backend subprocess output is decoded as UTF-8 regardless of the
host locale (a `C`/`POSIX` locale no longer mangles a Chinese/emoji answer); non-ASCII tool
args render natively in briefs. Test suite passes under `LC_ALL=C`.

## 0.1.0 — 2026-06-07

First public cut. A read-only "shadow-memory" sidecar for long-running coding
agents (Claude Code, Codex, …) — faithful, evidence-cited.

**Core (zero-dependency)**
- `brief` — deterministic, evidence-cited recap; every claim cites a transcript line.
- `check` — off-track / "is it safe to continue" judgment (fail-streaks, edit-thrash,
  retry-loops, stalls, failing tests), recency-weighted; scriptable exit codes.
- `status` / `fleet` — at-a-glance board of every session in a project, neediest first.
- `ask` / `chat` — grounded LLM Q&A over the cited state (never the raw transcript).
- `state --json`, `watch`, `sessions`.
- Faithful by construction: harness `isMeta`/`isCompactSummary`/`<synthetic>` noise
  filtered; failed edits excluded; cc-copilot's own narration sessions hidden.
- Adversarially audited (3 rounds, ~150 citations): 0 fabrications, 0 critical/major.
- 43 unit tests.

**Pluggable backends**
- codex (ChatGPT OAuth, default), claude, gemini, llm, and any OpenAI-compatible
  HTTP API (deepseek, openai, openrouter, ollama, …) — stdlib-only.
- `~/.cc-copilot.toml` for default backend/model/keys.

**Cockpit TUI** (optional `cc-copilot[tui]` extra; auto-bootstrapped on first run)
- Full-screen Textual cockpit: branded theme + verdict pill, split agent-timeline /
  chat panes, per-role gutters, multiline composer, command palette, collapsible
  output, modal session/model pickers, watcher toasts, Markdown answers — with
  `[L…]` citation fidelity preserved.
