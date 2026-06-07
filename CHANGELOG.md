# Changelog

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
