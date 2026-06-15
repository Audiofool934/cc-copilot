# Changelog

## 0.24.1 — 2026-06-15

**Ctrl+Z rolls the in-flight turn back into the composer.** Stopping a cockpit
answer no longer leaves a `⏹ stopped` row or partial assistant text in chat
history. The live prompt bubble and partial answer are removed, the stopped turn
is still not persisted, and the original submitted text is restored to the
composer for editing.

## 0.24.0 — 2026-06-15

**Project facts respect git, and the scan can't stall.** Building the read-only
project evidence for a chat turn used to walk the anchor session's whole cwd,
bounded only by how many text files it had *collected* — so launching from a
broad parent dir (an ML workspace whose subtrees hold large data/checkpoint dirs)
meant scandir-ing a huge tree on **every message**, a multi-second stall before
each send, while the same cockpit run from a code subdir was instant. Now the
discovery (1) **respects git** — in a work tree it enumerates files via
`git ls-files`, satisfying the file budget from **tracked** files first (an index
read, no worktree walk) and only falling to the untracked-but-unignored listing
if needed, so the data your `.gitignore` already excludes is never touched and a
big un-gitignored dir can't stall the per-turn build — and (2) falls back to a
**bounded** `os.scandir` walk for non-git roots, capped by entries visited and
wall-clock and streamed so the budget bails *before* a single giant directory is
even fully listed. Tunable via `CC_COPILOT_PROJECT_SCAN_MAX_ENTRIES` /
`CC_COPILOT_PROJECT_SCAN_TIME_BUDGET`. Secrets, binaries, and tracked-but-deleted
phantoms are filtered out of whatever git lists.

This matches how the field selects context files (`git ls-files` is the
.gitignore-respecting enumeration used by Cursor, Continue, Copilot, Windsurf,
Cody; Aider walks git's object tree to the same end), with a more defensive
non-git fallback than most tools document.

**The cockpit queues messages instead of dropping them.** Typing and sending a
second message while the current answer is still streaming used to be discarded
with "still answering the previous question". Now it's **queued** (FIFO, up to 8)
and sent automatically when the current turn finishes — fire off a few follow-ups
and walk away. The busy HUD shows `+N queued`; the queue clears on `/forget`,
`/rewind`, and session switches (those queued messages belonged to the old
context). Each queued turn builds its evidence at send time, so it reflects the
freshest state, and a queued message is dropped — never answered against the
wrong session — if you switch scope/session before it runs.

**Interrupt an answer with Ctrl+Z (or `/stop`).** "Queue by default, interrupt on
demand" — the pattern the field is converging on. Ctrl+Z (or `/stop` / `/cancel`)
now stops the in-flight answer without quitting the cockpit: the partial stays on
screen (marked `⏹ stopped`, not saved) and the pending queue is cleared so you can
steer. Previously the only way to halt was Ctrl+C, which quit the whole app.

## 0.23.0 — 2026-06-15

**`/since` reconciles edits against the real working tree.** The "Files changed"
list now marks each transcript-recorded edit `● uncommitted` (still pending in
`git status`) or `✓ committed/reverted since` (no longer in the tree), and flags
files that are dirty but **weren't edited in this session** (you or another
agent). So "the agent edited X" on a long run is no longer taken on faith — only
a read-only observer cross-checking the transcript against git can tell you the
edit is stale or that the tree diverged. Flows into `/handoff` too. Deterministic.

**The redaction chokepoint is now enforced.** Two standing guards
(`test_chokepoint.py`): every narrate entry point (`run_brief`/`ask`/`chat`/
`recap_since`/`next_step` and their streaming siblings) is verified to scrub an
injected secret before it reaches the backend, and a tripwire fails CI if any
module other than `narrate.py`/`backends.py` calls a backend directly — so model
traffic can never quietly bypass `redact()`. Recall (the corpus) only matters if
100% of traffic flows through the chokepoint; now both are guaranteed.

**Redaction is measured, and catches more.** A leak-corpus harness
(`test_redact_corpus.py`) asserts a hard recall floor against ~30 real secret
shapes in real contexts (env/JSON/YAML/bracketed, tool output, auth headers,
URLs, with citations) and names any leaker — so a dropped pattern fails loudly
instead of silently. Building it surfaced and fixed three gaps in the model-bound
redactor: **credentials in URLs/connection strings** (`postgres://app:PW@…`,
`https://user:PW@…`), **SendGrid `SG.` keys**, and a too-strict `ya29.` token
threshold. Ordinary evidence (git SHAs, `[L<n>]` citations, paths) still survives.

**`/since` shows how long you were away.** The re-entry header now reads
`since 14:31 (away 47m)` — the time-since-your-last-look, computed from the stored
last-look marker. It's the resumption-lag cue: the bigger the gap, the more you
need the recap.

**`/since` leads with your unanswered ask.** When the agent still owes you a
reply, `/since` now opens with `⏳ Your last ask is still unanswered: …` (cited),
above everything else — the suspended decision is the first thing a returning
human needs, and it shows even when nothing else changed while you were away.

**Cross-session collision radar.** `/status` now flags when the same file has been
mutated by 2+ sessions on **different branches** (`⚠ file collision(s) — same
file, different branches: cccopilot/tui.py — claude … ⎇feature · codex … ⎇main`).
This is the capability only a read-only, cross-agent observer can produce — an
in-process agent has no handle to a sibling session, and neither vendor can read
the other's transcripts, so nothing but a tool that unions Claude Code + Codex by
project cwd can catch two agents diverging on the same file. New
`cccopilot/collide.py`; paths canonicalized so absolute/relative forms match;
cross-branch divergence ranked first; bounded to recent fleet activity.

**Cross-agent fan-out on one fleet board.** The `/status` board now surfaces both
agents' fan-out — exactly where a human loses track of what's running:
- A **Claude** session that spawned subagents (child transcripts under
  `<session-id>/subagents/`) shows a `+Nsub` marker plus an indented rollup of the
  children by status (`↳ subagents: 1 running, 2 idle`) flagging any that need a
  look (stalled, or a review/intervene verdict). Children are parsed on demand for
  rendered rows only (the board isn't in the poll path), capped per parent.
- A **Codex** thread forked from another shows its parentage inline
  (`↰<parent> <nickname>`, e.g. `↰019d9971 Mill`), so a whole worker fleet is
  legible under its origin. No first-party view spans both agents' fan-outs.

**Fleet board shows each session's git branch.** A `⎇<branch>` column on `/status`
(both agents — Codex reads it from `session_meta.git.branch`) makes it obvious
who's off `main` and which sessions share a branch (a conflict risk).

**`/check` calibrates a CLEAR on long autonomous runs.** A CLEAR verdict on an
agent that's been running a long time since your last input now appends a
directional nudge — `running ~2h unattended (480 ev) — 'clear' is less certain
the longer it runs without a checkpoint; consider one`. Reliability decays with
horizon and agents don't self-recover from early mistakes, so an early human
checkpoint is disproportionately valuable. The verdict stays CLEAR; the hedge
uses only real observed numbers (duration, events), never a made-up reliability
statistic.

**Goal-drift heads-up (INFO).** `/observe` and `/check` now surface an info-level
note when recent work stops referencing the session's *originating* goal
("recent work no longer references the original goal … — confirm it's still on
track"). It's the first **INFO-severity** signal: by design it appears in the
signal list but never moves the CLEAR/REVIEW/INTERVENE verdict, so this
heuristic (stdlib keyword overlap against the first ask, conservatively gated)
can't cry wolf. Cross-agent.

## 0.22.0 — 2026-06-14

**Mid-session autonomy escalations are flagged (Codex).** State previously read
the sandbox/approval policy only from a session's first turn, so a later widening
went unseen. The adapter now records each turn's `turn_context`, and
`/check`·`/observe` flag — cited to the turn it changed — the genuinely notable
widenings: the sandbox jumping to full disk access (`danger-full-access`),
approval dropping to `never`, or network access newly enabled. Routine
read-only→workspace-write is intentionally not flagged.

**Exact Codex context pressure & rate limits surface as cited stall-causes.** The
adapter now reads Codex `token_count` events for the agent's real context
occupancy (`last_token_usage` vs `model_context_window` — not the cumulative
`total_token_usage`, which exceeds the window) and primary rate-limit usage.
`/check`/`/observe` flag, with the token_count line cited, when the agent is at
its rate limit ("silence may be the limit, not a stall") or its context is ~90%+
full ("it may compact or degrade soon") — an exact answer to "why is it quiet?"
no estimate can give. Codex-only; other sources are unaffected.

**Codex turns that end abnormally now surface.** The Codex adapter previously
discarded the entire `event_msg` stream; it now captures the two control signals
that live only there — a turn that was aborted/interrupted, and a surfaced error
— as cited `system` records (message/reasoning content is still skipped, so
nothing double-counts). `/check` and `/observe` flag a recent abort/error at the
tail ("your last turn ended early — it didn't finish") so a returning human is
oriented. (Approval-request events are not persisted in Codex rollouts, so
"blocked on approval" is intentionally not claimed.)

**Secrets are scrubbed from model-bound evidence (read-only invariant A).**
Before answering, cc-copilot now redacts secret-shaped content — API keys,
tokens, private-key blocks, auth headers, and secret-named `KEY=value` lines —
from the evidence copy sent to the LLM. Previously only secret-*named* files were
withheld (by basename), so an inline key in a tracked source file, a `tool_result`
echoing `cat .env`, or a token inside `AGENTS.md`/`CLAUDE.md` could reach the
model. Redaction is applied at the single narration chokepoint and touches the
model-bound copy only: the on-disk transcript, the `[L<n>]` citations, and the
cockpit's local display keep their real values. New `cccopilot/redact.py`.

**Agent narrator CLIs fail closed (read-only invariant B).** The `--tools ""`
(Claude) / `--sandbox read-only` (Codex) read-only flag is now applied
unconditionally, and cc-copilot refuses to launch an agent CLI as a narrator
when the installed CLI positively can't be confined to read-only — with an
actionable message to use an HTTP backend — instead of silently dropping the flag
and running it unguarded. A regression test guards that narrator backends are
never built without their safety gate.

**`/check` and `/observe` flag "says vs does" (claim-vs-evidence divergence).** A
new deterministic, REVIEW-only signal fires when a closing message claims an
outcome the turn's own evidence doesn't back: (A) "tests/build pass" with no
passing test or build result this turn, or (B) "fixed it" after editing code with
nothing run to verify. It surfaces a cited pair (the claim line + the missing
evidence) to check — never an accusation, never an INTERVENE driver, and it
ignores negated statements ("not all tests pass"). Works across Claude Code and
Codex sessions.

**Raw cited evidence leads the model context pack.** Position-aware packing moves
the primary raw transcript records to the head of the evidence pack (out of the
"lost in the middle" zone) so they're attended to first and never truncated for
lower-priority project facts or the navigation-only summary index under budget
pressure.

**Parsers are hardened against pathological session files.** A per-line read cap
bounds memory so a multi-MB/GB single line — a giant `tool_result` or a Codex
`Compacted.replacement_history` blob (issue #24948) — can't exhaust the cockpit;
the line is counted as a parse error and the surrounding records survive with
their `[L<n>]` citations intact. cc-copilot still never locks or rewrites the
agent's files.

## 0.21.0 — 2026-06-13

**Slash commands take an inline steer.** `/now` and `/since` now accept free
text after the command — `/now in spanish`, `/since 2h just the blocker`,
`/since as bullets`. The steer shapes *how* the grounded recap/recommendation
reads (language, tone, length, focus) but can't loosen the evidence grounding:
the "use only this evidence, never invent facts" contract is restated to the
model. `/now` keeps its instant bare path; the steer is optional.

**`/command` results render as real Markdown, inline.** `/now`, `/since`,
`/brief`, `/observe`, `/check`, and `/handoff` output rendered headings, bold,
and rules — instead of raw `#`/`**`/`---` characters sitting in a plain box. The
collapsible "layer" is gone; results flow like a reply with an accent gutter bar.
Pre-formatted boards (`/status`, `/diff`, `/target`, `/help`) keep their columns
verbatim.

**Chat messages are timestamped.** Each turn shows a dim `HH:MM` hard against the
right edge, with a `you` / `copilot` role label on the left. Restored history
uses each turn's real recorded time; live turns stamp the moment they happen.

**`/since` header is time-anchored.** The recap header now reads
`since 14:31 · 9 new lines` instead of the raw `watching up to L0 → now L9` line
span — consistent with the cockpit's `HH:MM` convention.

**Notifications moved to the top-right and toned down.** Toasts no longer cover
the prompt box at the bottom — they sit in the upper-right corner, slimmed to a
single-row, auto-width chip with the cockpit's accent bar instead of a wide,
deeply-padded block.

## 0.20.1 — 2026-06-13

**Fix: Ctrl+Y now actually copies.** The new copy key was bound without priority,
so the focused composer (a `TextArea`) swallowed `ctrl+y` before the app's binding
could fire — pressing it did nothing. It's now a priority binding (checked before
the focused widget), with a regression test that presses the key from the focused
composer. Shipped broken in 0.20.0.

## 0.20.0 — 2026-06-13

**Copying text from the cockpit is now one key.** Drag to select any message
text — the tip line above the composer prompts *"Ctrl+Y to copy"* — then
**Ctrl+Y** copies it to your system clipboard as clean text (no role-bar or
borders). It works locally *and* over tmux/SSH: OSC 52 for the remote case, plus
a local `pbcopy` / `wl-copy` / `xclip`, so it also works in terminals where OSC 52
no-ops (notably macOS Terminal.app). This replaces the
over-engineered `/select` / `/copy-mode` / Ctrl+N "release the mouse to the
terminal" mode and its paragraph of Option/Fn/⌘C instructions, all removed.
Ctrl+C stays bound to quit so it's never ambiguous; ⌘C is intercepted by the
terminal and can't reach the app.

**Switching model in the cockpit can now update your default.** After a `/model`
switch (backend or model), the cockpit asks *"make this the default for new
cockpit sessions?"* — answer yes and it writes the new `backend`/`model` to
`~/.cc-copilot.toml` (preserving your `[env]` secrets, history, and agents),
so the next new session starts where you left off instead of always reverting to
your original setup choice. It only asks when a config already exists and the
choice actually differs from the saved default, and never on `/init` or key
capture (those already write the config). A new `onboard.persist_default` does
the surgical, atomic, 0600 config update for any backend (including non-curated
ones like ollama that `/model` can reach).

## 0.19.0 — 2026-06-13

**Slash-command organization pass.** A multi-surface audit (REPL / cockpit TUI /
CLI / docs) drove a round of consistency fixes:

- **`/now` owns "what next."** `/since`'s recap and the `--narrate` orientation no
  longer also prescribe the next action — they recap and orient; the next-step
  recommendation is `/now`'s sole job. Removed the dead `narrate()` /
  `narrate_brief()` helpers (zero callers; only the `--narrate` streaming path
  survives).
- **`/session` → `/target`.** The singular readout was one keystroke from
  `/sessions` and meant different things on different surfaces (a readout in the
  REPL, an alias of `/sessions` in the cockpit). It is now `/target` everywhere —
  a single, consistent "current cockpit target" readout — and `/sessions` is the
  sole evidence picker.
- **`/status` in the cockpit.** The fleet board (every session in the project,
  neediest first) is now reachable from the REPL and the cockpit, not just
  `cc-copilot status`. The board renderer is shared across all three.
- **Help-text consistency.** Aligned the one-liners for `observe` / `brief` /
  `check` / `handoff` / `since` across every surface (incl. the `1d` `/since`
  window the TUI and README had omitted), gave `/now` its `(LLM; deterministic
  fallback)` marker in the cockpit autocomplete, listed `/clear` in the cockpit
  help, and documented the intentionally-hidden power aliases (`/scope`,
  `/history`, and the short spellings) in a comment rather than leaving them
  silently undiscoverable.

**New `/now` command — "what should I do next?"** After running something
through an agent, `/now` recommends the next step: an LLM recommendation grounded
in the read-only evidence of the completed work (it keeps the `[L…]` citations
and never invents), with a deterministic next-step — the observer's ranked
decision — as the always-true fallback when no backend is available, on error, or
with `--raw`. Scope-aware like `/brief` / `/observe` / `/check`, and available on
every surface: the chat REPL (`/now`), the cockpit TUI (`/now`, the command
palette, and `/` autocomplete; the model call runs off the UI thread and is
dropped if you switch evidence while it runs), and the CLI (`cc-copilot now`,
`--raw` for the deterministic next-step alone).

**Sibling discovery no longer drops sessions in a different projects bucket.**
`_candidate_refs` found the watched agent's own Claude sessions two ways — a scan
of the anchor's directory plus a `cwd`-based lookup — and wrongly assumed the
directory scan was a superset, skipping every Claude entry from the `cwd` lookup.
When the two reach different `~/.claude/projects/<bucket>/` directories (e.g. a
moved session, or an anchor whose on-disk directory encodes differently than its
recorded `cwd`), a real sibling could be dropped. Discovery now unions and dedups
by path instead.

**Robustness pass: 17 crash / data-loss / wrong-output fixes.** A package-wide,
adversarially-verified audit fixed a batch of pre-existing defects across the
core runtime:

- *Persistence.* `truncate()` (rewind/fork) held its lock on the very log file
  it then atomically replaced, so a concurrent turn from a second cockpit — or
  the TUI's own answer worker thread — could be written into the orphaned inode
  and silently lost while the stored turn count drifted. The lock now lives on a
  stable per-conversation lock file held across every mutation, and
  `record_state` re-derives the turn count from the log instead of writing back
  a stale cached value.
- *Missing-file resilience.* `watch`, `status`/`fleet`, `/sessions`, `/use`,
  `/here`, and the cockpit activity strip no longer crash when an observed
  transcript is deleted or rotated mid-operation — each degrades and keeps going.
- *Malformed-input parsing.* A non-numeric session `updatedAt`, a slash-only
  command name, a Unicode-digit session selector, a non-object `meta.json`, a
  null cockpit-history entry, and an `[agents]` array under the no-tomllib
  fallback parser (which silently disabled agent discovery on Python 3.9/3.10)
  are all handled now instead of raising.
- *Output correctness.* Multi-session "recent evidence" orders by wall-clock
  time rather than per-file line number (a long stale session no longer buries a
  short fresh one); Codex image/structured tool outputs render an `[image]`
  placeholder instead of dumping kilobytes of base64; a `null` completion body
  surfaces as a clean backend error; rewinding the chat while an answer is still
  streaming no longer resurrects that abandoned turn into the fork; and `handoff`
  keeps its "While you were away" section for status/safety transitions that
  carry no counted events.

**Read-only narrator is enforced, not just requested.** The Claude and Codex CLI
backends are now launched with their own read-only/non-persistent flags
(`--tools ""`, `--no-session-persistence`, `--safe-mode`, `--strict-mcp-config`,
`--disable-slash-commands` for Claude; `--sandbox read-only`, `--ephemeral`,
`--ignore-rules`, `--ignore-user-config` for Codex) so the supervision layer
cannot become a tool-using agent even if a prompt told it to. Every flag is
gated on the installed CLI's own `--help` — with a token-boundary match so a
look-alike flag like `--tools-config` never enables a flag the CLI would reject
— so older builds that lack a flag still run.

**Project evidence is harder to leak secrets through.** The deterministic file
scanner that feeds `/brief`, chat, and project context now skips a wider set of
credential files and trees (cloud/k8s/terraform dirs, shell and DB history
files, `.pgpass`/`.htpasswd`/`.dockercfg`, GCP/Firebase service-account and
token JSON, keystores). The expanded matching was also tightened so it no longer
drops ordinary source files — `secrets.py`, `credentials.go`, and
`service_account.go` stay in the index; the varying-basename credential blobs
(`firebase-adminsdk-*.json`) are matched only against `*.json`.

**`backends` tells the truth about no-key endpoints.** `cc-copilot backends`
now probes keyless local/custom OpenAI-compatible endpoints (e.g. Ollama)
before reporting "ready", reports an invalid active backend as a clean error
with exit code 2 instead of a traceback, and a malformed `CC_COPILOT_LLM_CMD`
(unbalanced quotes) now surfaces as a graceful error rather than crashing
narration. `CC_COPILOT_LLM_CMD` is parsed with shell-style quoting.

**`chat --next` pins the session it waited for.** The session path returned by
the wait is now actually used, instead of falling back to whatever the resolver
picked.

**Simpler model picker.** Removed the niche long-tail provider backends from the
built-in `/model`, `backends`, and `init` surfaces. The public provider list now
stays focused on Claude, Codex, DeepSeek, Gemini API, GLM, Groq, Grok, Kimi,
Ollama, OpenAI, OpenRouter, Qwen, and custom OpenAI-compatible endpoints.
Claude, Codex, and DeepSeek keep their alphabetical positions but now carry
distinct brand colors in the interactive model lists.

## 0.18.1 — 2026-06-12

**Sticky prompt positioning fixes.** The chat-top sticky prompt now follows the
actual scroll position instead of only the last keyboard jump: short
conversations keep the latest or manually selected prompt, long conversations
sync the `N/total` count to the prompt owning the current top of the viewport,
and exact boundary cases no longer switch one line early when the next prompt is
just below the top edge. Left/Right jumps force the target prompt to the top of
the chat pane when possible, with a fallback for older Textual versions.

**Router model-id switching is less surprising.** While a router-style backend
such as OpenRouter, Groq, Together, DeepInfra, Hugging Face, NVIDIA, Chutes,
Novita, or GMI is active, slash-style model ids like `openai/gpt-6-preview` stay
on the current backend instead of being interpreted as a provider switch.
Explicit backend switches still use the existing `backend:model` form.

## 0.18.0 — 2026-06-12

**Codex-aware `/here`, richer model switching, and faster cockpit browsing.**
The cockpit can now attach to the current live Codex session as well as Claude:
`/here` uses source adapters instead of Claude-only environment guesses, so a
cc-copilot pane opened beside Codex can jump straight onto that live rollout and
show it as `your live session`.

**`/model` now reaches the long tail.** The curated API catalog was expanded
from the original provider set using OpenClaw's provider catalog shape and
coverage: Mistral, Together, Fireworks, Cerebras, DeepInfra, Hugging Face
Router, NVIDIA, Chutes, Novita, Venice, Arcee, GMI, StepFun, Xiaomi,
Volcengine, and Tencent TokenHub now have registry entries, curated defaults,
model-picker rows, config comments, and inline key prompts. Existing catalogs
were refreshed too: DeepSeek still defaults to `deepseek-v4-flash` (not
`deepseek-chat`), and `/model` accepts provider refs such as
`/model openai/gpt-5.5`, `/model google/gemini-3.1-flash-lite`, and
`/model openrouter/moonshotai/kimi-k2.6` without breaking OpenRouter model ids
that naturally contain slashes or colon suffixes.

**Composer and chat navigation feel more terminal-native.** Prompt history is
available with Up/Down in the input box; Esc clears a draft, and double-Esc on
an empty box opens rewind. The chat pane also has a one-line sticky prompt
header showing the selected prior prompt's first line. With an empty input box,
Left/Right jumps between prior prompts in the chat, and clicking that sticky
line jumps back to the pinned prompt.

## 0.17.1 — 2026-06-11

**`launch` split-screen fixes (user-reported).** The cockpit pane is now a
third of the window (agent : cockpit = 2 : 1) instead of half — the agent is
the main act. And the session `launch` creates gets `mouse on`: stock tmux
ships with the mouse off, where clicking a pane does nothing — so anyone not
fluent in tmux prefix keys literally could not focus the cockpit pane (every
keystroke kept landing in the agent). Click-to-focus and wheel scrolling now
work out of the box in launch-created sessions; when `launch` splits inside
*your* tmux, your options are left untouched. tmux quirk for the record:
`set-option -t` rejects the `=exact` target prefix that `has-session` /
`kill-session` accept — bare name used (exact match guaranteed, the session
was just created).

## 0.17.0 — 2026-06-10

**One command to start it all.** `cc-copilot launch` (alias `up`) starts your
agent and the cockpit side by side in a tmux split — inside tmux it splits the
current window and the agent takes over your pane; outside it creates a
`cc-copilot` session with both panes and attaches. The agent command is
whatever you say (`launch codex`, `launch -- claude --resume`; default: claude,
else codex). Without tmux it says so and opens the cockpit alone. The cockpit
side rides the new `--next` flag: wait for the project's transcripts to
*change* — a new session appearing, or an existing one growing (`claude
--resume` appends in place) — and pin to it, instead of latching onto
yesterday's transcript or dying in a fresh project. And plain `cc-copilot`
with no arguments now opens the cockpit — off-TTY (scripts, hooks, pipes) the
usage error stays. Hardened against the launch-pane-dies-with-the-diagnostic
family: `[tui]` preflight before any pane opens, the agent binary absolutized
(the tmux *server's* PATH resolves pane commands), `env`-prefixed cockpit
command (fish/tcsh default-shells), and no leaked half-built sessions on
setup failure.

## 0.16.0 — 2026-06-10

**Pick your model, not just your provider — plus 6 new providers.** Every API
backend now carries a small curated model catalog (`cccopilot/models.py`, one
hand-checked table verified against live provider docs), and the cockpit's
`/model` flow is two-level: pick the backend, then pick among its models —
with typed fast-paths `/model deepseek-v4-pro` (switch model on the current
backend), `/model deepseek:deepseek-v4-pro` (both at once), and any free-form
id still accepted everywhere (the catalog is a convenience, never a
restriction). `cc-copilot backends --models` lists everything; the `init`
wizard offers a numbered model menu.

- **DeepSeek defaults to `deepseek-v4-flash`** (with `deepseek-v4-pro`
  selectable) — `deepseek-chat` / `deepseek-reasoner` are deprecated upstream
  on **2026-07-24** and are marked as such in the picker. OpenAI's default
  moves `gpt-4o` → `gpt-5.4`.
- **New providers** (all OpenAI-compatible, stdlib HTTP, key via env or the
  config's `[env]` table): **moonshot** (Kimi `kimi-k2.6`), **zai** (GLM
  `glm-5.1`), **qwen** (DashScope, `qwen3-max`; mainland endpoint via
  `DASHSCOPE_API_BASE`), **groq**, **xai** (Grok `grok-4.3`), and
  **gemini-api** (Google's OpenAI-compat endpoint — distinct from the `gemini`
  CLI backend). All get the inline key-prompt on `/model` switch, like the
  original three.
- **Fix:** a model picked for any provider used to leak into **ollama's**
  default via the `CC_COPILOT_MODEL` export (cross-provider contamination) —
  ollama now has its own catalog default.
- Model switches stay session-scoped like backend switches; persist a default
  with `cc-copilot init` (which now also preserves your other settings, as
  before). The first-run welcome modal stays compact (featured providers);
  `cc-copilot init` lists every provider.

## 0.15.0 — 2026-06-10

**Streaming answers — the copilot talks while it thinks.** Chat answers in the
cockpit and the REPL, `cc-copilot ask`, and `brief --narrate` now render
incrementally as the model produces them instead of blocking on a spinner until
the full answer lands. Every backend streams through one new seam
(`Backend.stream()` with a blocking single-chunk fallback, so custom CLIs and
older agent builds keep working unchanged):

- **claude** — true token-level deltas via `claude -p --output-format
  stream-json --verbose --include-partial-messages`, gated on a one-time
  `--help` capability probe so older CLIs fall back cleanly.
- **codex** — `codex exec --json` lifecycle events (codex emits the message
  whole; the win is exact usage below). `stdin` is now closed for CLI backends
  so a piped stdin can never hang `codex exec`.
- **OpenAI-compatible HTTP** (deepseek/openai/openrouter/ollama/custom) —
  stdlib-only SSE (`stream: true`) with a graceful degrade ladder: a 400/422
  retries once without `stream_options`, a second 400/422 falls back to the
  blocking request (providers that never supported streaming keep working
  unchanged), a server that ignores `stream: true` and answers with one JSON
  body is parsed anyway, and auth/server errors (401/403/404/5xx) surface
  immediately with no retries.

**Exact token usage replaces the chars/4 guess where the backend reports it.**
The claude result event (tokens + real `$` cost), the codex `turn.completed`
event, and the SSE/blocking `usage` object now flow into the HUD — `out 1.2k`
(no `~`) when exact, plus the turn's cost for claude — and into each persisted
turn in `turns.jsonl` (`"usage": {...}`).

Faithfulness holds under streaming, by construction:

- a partial answer is **never persisted** — history and `turns.jsonl` are
  written only after a stream completes; a mid-stream error keeps the partial
  text visible with an explicit "partial answer above was not saved" note;
- chunks for a conversation you switched away from are dropped on screen while
  the completed turn still lands in **its own** store (unchanged contract);
- `/clear` mid-stream re-mounts the answer with the full accumulated text, so
  the visible answer never silently loses its head;
- the REPL holds its print lock for the whole stream, so background alerts
  queue and print after the answer instead of splicing into it.

Cockpit rendering uses `Markdown.append` (trailing-block re-parse) with 50 ms
worker-side coalescing — claude's token deltas paint as words, not keystrokes —
and the chat pane anchors to the bottom while streaming, released the moment
you scroll up. `CC_COPILOT_STREAM=0` opts out (everything then behaves exactly
as before).

Hardened by three cross-model review rounds plus an 18-agent adversarial
workflow (every finding re-verified, then fixed): quitting the cockpit
mid-answer now aborts the backend transport instead of hanging up to the
stream timeout (`Backend.cancel()`); `cc-copilot ask … | head` no longer
surfaces a spurious BrokenPipeError; chunks that stream in while you're
viewing another conversation buffer and repaint in full when you switch back;
`/forget` aborts only an answer running for the forgotten conversation; exact
usage/cost can't leak onto another conversation's HUD; and the stderr-drain
race, SSE-BOM first-chunk loss, and claude multi-message mashing are gone.
44 new tests (368 total).

## 0.14.1 — 2026-06-10

**Fix: switching to an API provider via `/model` now prompts for its key.** The
quick backend switch (`/model deepseek`, `/model` picker, or `Ctrl+T`) used to
set an API provider silently even with no key on file — `resolve()` succeeds
without one, so the switch "worked" but the next chat failed with `set
DEEPSEEK_API_KEY`. It now opens a focused key prompt (the same persistence as
onboarding: written to the chmod-600 `[env]` table, a real env var still wins),
and **Cancel** keeps your current backend. Switching also keeps the active model
coherent with the new backend's kind — an API provider adopts its default (e.g.
`deepseek-chat`); a CLI backend drops any stale API model so a
claude→deepseek→claude round-trip never runs `claude --model deepseek-chat`.

## 0.14.0 — 2026-06-10

**First-run onboarding — pick your model once, instead of silently defaulting.**
The first time you launch the cockpit (no `~/.cc-copilot.toml` yet), a branded
**welcome screen** asks which model should power recaps, chat, and `since`
summaries — **Claude** or **Codex** (uses the agent's own login, no key) or an
**API provider** (OpenAI / DeepSeek / OpenRouter, with the key captured inline
and written to the chmod-600 `[env]` table). It shows only on the first run; the
config's existence is the "already set up" sentinel.

- **`cc-copilot init`** — the same wizard in a plain terminal (line-based menu +
  hidden key prompt), for headless / SSH setup or to reconfigure later
  (`--force` to rewrite; other providers' keys and your `[history]` setting are
  preserved across a re-run).
- **`/init` in the cockpit** reopens the picker anytime.
- Picking a model **takes effect immediately** in the running cockpit — no
  relaunch. Non-cockpit LLM commands (`ask` / `since` / `brief --narrate`) print
  a one-line first-run nudge until you choose. Everything stays scriptable:
  onboarding never fires on a non-TTY (hooks/CI) or when `--backend` is explicit,
  and `CC_COPILOT_NO_ONBOARD=1` opts out entirely.
- The shared, UI-agnostic core lives in `cccopilot.onboard` (zero-dep, fully
  unit-tested), so the TUI screen and the terminal wizard can't drift.

**Cockpit chrome: slimmer footer + a rotating tip line.** The footer now shows
only the few highest-value keys (`model · select · palette · quit`); refresh,
clear, and the `Shift+↑/↓` timeline-resize keys are still bound but no longer
crowd the bar. Their discoverability moves into a new **subtle, rotating tip
line** above the composer — one muted `💡 …` line that cycles a curated set of
20 feature tips (shuffled, non-repeating), each ≤64 chars so it survives a narrow
sidebar, ordered "most useful when you just got back" first. The composer hint is
trimmed to `Enter send · Ctrl+J newline · / commands`, and the welcome modal was
widened so API rows don't clip.

## 0.13.3 — 2026-06-09

**Maintenance: the version is single-sourced.** `pyproject.toml` now reads the
version dynamically from `cccopilot/__init__.__version__`, so cutting a release
is a **one-line** bump (no more keeping two files in lockstep). The release
workflow's guard validates the *built wheel's* version against the tag, so a
mismatch still can't publish. No runtime changes.

## 0.13.2 — 2026-06-09

**Installable in one command — published to PyPI.** No more cloning the repo:

```
uv tool install "cc-copilot[tui]"      # or: pipx install "cc-copilot[tui]"
uvx --from "cc-copilot[tui]" cc-copilot cockpit   # run without installing
```

- **Packaging fix (was ship-blocking):** the wheel listed `packages = ["cccopilot"]`,
  which silently dropped the `cccopilot/sources/` subpackage (the Claude/Codex
  adapters) — a plain `pip install` would have import-errored. Now uses
  `packages.find` so every subpackage ships; verified by a clean-venv install.
- **`cockpit` no longer writes a `.venv` into an installed package.** The
  one-time TUI bootstrap was a *clone* convenience; when cc-copilot is installed
  via pip/uv/pipx it now detects that and points you at the `[tui]` extra instead
  of trying to build a `.venv` inside site-packages (which would pollute a
  writable tool env and fail a read-only one).
- **Release automation:** a GitHub Actions workflow publishes to PyPI via
  Trusted Publishing (OIDC, no API tokens) on every `v*` tag, with a build that
  runs `twine check`, a tag↔version guard (both `pyproject.toml` and
  `__init__.py`), and a wheel install smoke test. See `docs/RELEASING.md`.
- Added `[project.urls]`, Python-version/OS classifiers, and README install
  badges.

## 0.13.1 — 2026-06-09

**You can copy out of the cockpit now (`Ctrl+N` / `/select`).** Textual captures
the mouse for scroll/click, which blocks the *terminal's* own click-drag
selection — so dragging did nothing and ⌘C had nothing to copy. `Ctrl+N` (or
`/select`) now toggles **select mode**: it hands the mouse back to the terminal
so you can drag-select and ⌘C exactly like a normal shell, with a clear status
banner; toggle again to restore wheel-scroll and clicks. For a one-off without
toggling, hold Option (iTerm2) / Fn (Terminal.app) while dragging. (Mouse-capture
release uses the driver's own mouse-support hooks; no architecture change.)

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
