# CC-Copilot

[![PyPI](https://img.shields.io/pypi/v/cc-copilot)](https://pypi.org/project/cc-copilot/)
[![Python](https://img.shields.io/pypi/pyversions/cc-copilot)](https://pypi.org/project/cc-copilot/)
[![CI](https://github.com/Audiofool934/cc-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/Audiofool934/cc-copilot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**CC-Copilot helps you stay aligned with AI coding agents.**

Cockpit is the primary TUI experience: a live supervision layer for ongoing
agentic work, shared context, and next decisions.

Ask questions across one session, selected sessions, or an entire project
without injecting supervision chatter into the main Claude Code or Codex
workflow.

![CC-Copilot demo — re-entry recap, grounded chat, model picker](docs/demo.gif)

*Above: coming back to an agent session — the `/since` re-entry recap of what
happened while you were away, a streamed citation-pinned answer to "is it safe
to merge?", and the two-level model picker.*

## Quick Start

```bash
cc-copilot cockpit
```

The **first launch** greets you with a one-time welcome screen to pick the model
that powers recaps, chat, and `since` — **Claude** or **Codex** (uses the
agent's own login, no API key) or an **API provider** (OpenAI / DeepSeek /
OpenRouter, key captured inline). It only shows once; reopen it anytime with
`/init`, or run `cc-copilot init` in a plain terminal (handy over SSH). The
deterministic core (`brief` / `check` / `observe`) needs no model at all.

![CC-Copilot first-run welcome screen](docs/welcome.png)

Inside Cockpit:

```text
/sessions   choose one or more agent sessions (incl. your own live session)
/here       observe the session you're running inside of
/observe    attention queue and next human decision
/since      recap since you last looked (or 30m / 2h; --raw = cited delta)
/handoff    shareable Markdown handoff (brief + what changed)
/brief      deterministic recap with citations
/check      safety / off-track verdict
/diff       changes since last turn
/model      choose backend/model
/init       reopen the model picker (Claude / Codex / an API key)
/resume     resume a Cockpit Session
```

## Install

One command, no clone — install it as an isolated tool:

```bash
uv tool install "cc-copilot[tui]"      # recommended (https://docs.astral.sh/uv/)
# or
pipx install "cc-copilot[tui]"
```

Then:

```bash
cc-copilot cockpit
```

Or run it without installing anything:

```bash
uvx --from "cc-copilot[tui]" cc-copilot cockpit
```

Requirements: Python 3.9+. The CLI core is **dependency-free**; the Cockpit TUI
pulls in the optional `[tui]` extra (Textual) — drop it (`cc-copilot` instead of
`cc-copilot[tui]`) if you only want the command-line briefs.

<details>
<summary>plain <code>pip</code> / from source</summary>

```bash
pip install "cc-copilot[tui]"                         # from PyPI
# from a clone (development):
pip install -e ".[tui]"
```

On fresh Debian/Ubuntu servers `pipx`/venv may need:

```bash
sudo apt-get update && sudo apt-get install -y python3-venv python3-pip pipx
```
</details>

## Why CC-Copilot

Past copilots reduced the cognitive burden of understanding code.

Agentic coding creates a new burden: understanding what agents are doing over
time. Long-running Claude Code, Codex, and multi-agent workflows produce
continuous context, decisions, tool calls, errors, and partial progress.

CC-Copilot gives humans a separate supervision layer. You can inspect, ask,
compare, and realign without interrupting the working agent or forcing yourself
to reconstruct context manually.

## What Makes It Different

1. **Read-only supervision**
   CC-Copilot observes agent transcripts and project context without editing
   files, issuing agent actions, or interfering with the working agent.

2. **Separate Cockpit workflow**
   Ask supervision questions outside the main agent conversation, so the
   agent's working context stays clean.

3. **Evidence-grounded answers**
   Answers are grounded in transcript lines, tool results, project facts, git
   state, and file evidence.

4. **Multi-session awareness**
   Follow one session, selected sessions, or an entire project from one
   interface.

5. **Attention-first UI**
   Cockpit surfaces status, risk, progress, and the next human decision instead
   of forcing you to read the whole transcript.

6. **Model-flexible**
   Use Codex, Claude, OpenAI-compatible APIs, DeepSeek, OpenRouter, Ollama,
   Gemini, or custom CLI backends.

7. **Terminal-native**
   Keyboard-first TUI with mouse support, designed for side-window and
   CMUX-style workflows.

8. **Resumable Cockpit Sessions**
   Your supervision conversation is independent from the agent session and can
   be resumed later.

## Usage

```bash
cc-copilot cockpit                 # open the TUI
cc-copilot sessions                # list project sessions
cc-copilot status                  # fleet board, neediest first
cc-copilot observe                 # attention queue
cc-copilot brief                   # deterministic recap
cc-copilot check                   # safety verdict
cc-copilot since                   # grounded LLM recap since you last looked
cc-copilot since 30m               # …or within a time window
cc-copilot since --raw             # the deterministic cited delta, no model call
cc-copilot handoff --out h.md      # shareable Markdown handoff
cc-copilot watch --notify          # desktop alert when the agent needs you
cc-copilot ask "what changed?"     # one-shot grounded Q&A
cc-copilot chat                    # plain terminal chat mode
cc-copilot resume                  # resumable Cockpit Sessions
```

Scope options:

```bash
cc-copilot cockpit                 # one agent session by default
cc-copilot cockpit --scope multi   # selected/all sessions in this project
cc-copilot cockpit --scope project # project-level evidence context

cc-copilot ask --scope multi --scope-sessions a1b2c3d4,b5c53c29 "compare these"
cc-copilot observe --scope project
```

Session discovery spans every coding agent with sessions on this machine:

```text
${CLAUDE_CONFIG_DIR:-~/.claude}/projects/<encoded-cwd>/<session>.jsonl   # Claude Code
${CODEX_HOME:-~/.codex}/sessions/YYYY/MM/DD/rollout-*.jsonl              # Codex
```

A project's sessions from both agents appear on one board, grouped by project
cwd and tagged with their agent. Restrict the set with `--agent claude`,
`--agent codex`, `$CC_COPILOT_AGENTS`, or `[agents] enabled` in the config.

By default, commands report on the most recent session other than the current
one, so running CC-Copilot from inside a live agent session watches the agent
you want to supervise. To watch your **own** current session instead, use
`--here` (e.g. `cc-copilot cockpit --here`) or `/here` inside the cockpit — it is
also always listed in `/sessions` as "your live session", even across projects.
See [docs/cross-model-adapters.md](docs/cross-model-adapters.md).

## Cockpit

Cockpit is the main product surface.

It gives you:

- Status header for project, evidence range, Cockpit Session, backend, and risk.
- Live activity strip from the observed session(s).
- Attention queue and next human decision via `/observe`.
- Grounded chat over one session, selected sessions, or project evidence.
- Context HUD showing estimated input context, output estimate, and evidence
  split across raw transcript, project facts, chat, memory, and summary index.
- Background alerts when the agent stalls, errors, or goes off track.
- Checkbox session picker with `[ ]` / `[x]` multi-select.
- Resumable Cockpit Sessions via `/resume`.

Keyboard is primary; mouse works too. Click anywhere to return focus to the
composer. `Enter` sends, `Ctrl+J` inserts a newline, `/` opens command
suggestions, and `Ctrl+P` opens the command palette. `Shift+↑` / `Shift+↓` resize
the activity timeline (the chat fills the rest); the height is remembered across
launches.

## While You Were Away

The hardest part of supervising long-running agents is *re-entry*: you stepped
away, the agent kept working, and now you have to reconstruct what happened.

- **`since`** answers "what changed since I last looked" — by default a short
  **LLM recap narrated from** the deterministic, cited diff of new asks, agent
  messages, commands, failures, changed files, and any status/safety transition
  (the model sees only that cited delta and keeps its `[L…]` citations; `--raw`
  or no backend gives the deterministic delta itself). cc-copilot remembers where
  you last looked (a
  small marker under `$CC_COPILOT_STATE_DIR`, never under `~/.claude`/`~/.codex`);
  the cockpit stamps it when you leave and greets you with "⟳ N new since you last
  looked" when you return. Or scope by time: `since 30m`, `since 2h`.
- **`handoff`** turns the current state into a shareable Markdown document — the
  brief plus an optional "while you were away" section — to paste into a ticket
  or hand to a teammate. Every line keeps its `[L…]` citation.
- **`watch --notify`** pings you (desktop notification, terminal-bell fallback)
  only when a session *crosses into* needing you — a fresh intervene verdict, a
  slide into stalled, or a new failure — so you can step away without missing the
  moment it actually needs a human.

All three are LLM-free and work across Claude Code and Codex sessions.

## Evidence Context

v0.7 introduced the Evidence Context Engine.

For model-backed `ask`, `chat`, and Cockpit answers, CC-Copilot now retrieves
primary evidence first:

- raw assistant messages
- raw human prompts
- tool calls and tool results
- cited line windows
- recent transcript tail
- selected multi-session records
- read-only project facts
- git/file evidence
- Cockpit conversation memory

Summaries still exist, but they are navigation aids and UI surfaces, not the
only source of truth. See [docs/evidence-context-engine.md](docs/evidence-context-engine.md).

## Models

```bash
cc-copilot backends
cc-copilot cockpit --backend codex
cc-copilot cockpit --backend claude
cc-copilot ask "what matters next?" --backend openai --model gpt-4o
```

Supported backend families:

| backend | authentication | notes |
|---|---|---|
| `codex` | your `codex login` | default; local agent CLI |
| `claude` | your Claude Code login | `claude -p`; no API key |
| `gemini` / `llm` | the CLI's own config | if installed on PATH |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` default (`-chat` is deprecated 2026-07-24) |
| `openai` | `OPENAI_API_KEY` | `gpt-5.4` default |
| `openrouter` | `OPENROUTER_API_KEY` | any OpenRouter model |
| `moonshot` | `MOONSHOT_API_KEY` | Kimi (`kimi-k2.6`) |
| `zai` | `ZAI_API_KEY` | GLM (`glm-5.1`) |
| `qwen` | `DASHSCOPE_API_KEY` | Qwen via DashScope; mainland endpoint via `DASHSCOPE_API_BASE` |
| `groq` | `GROQ_API_KEY` | fast open-weights hosting |
| `xai` | `XAI_API_KEY` | Grok (`grok-4.3`) |
| `gemini-api` | `GEMINI_API_KEY` | Google's OpenAI-compat endpoint (≠ the `gemini` CLI) |
| `ollama` | none | local server at `http://localhost:11434` |
| `custom` | `CC_COPILOT_API_BASE` or `CC_COPILOT_LLM_CMD` | proxy/API/CLI escape hatch |

Each API provider ships a small **curated model list** (see `cc-copilot backends
--models`): the `/model` picker offers them two-level — backend, then model —
and typed fast-paths work too: `/model deepseek-v4-pro` (model only),
`/model deepseek:deepseek-v4-pro` (both at once), or any free-form id.

Answers **stream** as the model produces them (claude: token deltas; the HTTP
backends: SSE; codex: whole message + exact usage), and when the backend
reports exact token usage it replaces the HUD's `~` estimate — including the
turn's real cost for claude. `CC_COPILOT_STREAM=0` opts out of streaming.

Set defaults once — the guided way (pick a model, capture an API key):

```bash
cc-copilot init           # interactive wizard (also runs on the first cockpit launch)
```

Or scaffold/inspect the file directly:

```bash
cc-copilot config --init  # write a commented starter ~/.cc-copilot.toml
cc-copilot config         # show the effective backend
```

Example `~/.cc-copilot.toml`:

```toml
backend = "codex"
# model = "..."

[history]
enabled = true
```

Precedence: explicit flags > environment variables > config file > built-in
default.

## Read-Only Contract

CC-Copilot is an observer.

It reads:

- agent transcripts
- session metadata
- read-only project facts
- git status
- cited file excerpts
- saved Cockpit conversation state

It does not:

- mutate the observed agent session
- edit project files
- run tools on behalf of the observed agent
- write under `~/.claude`
- inject supervision chatter into Claude Code or Codex

Deterministic commands work without an LLM:

```bash
cc-copilot brief
cc-copilot observe
cc-copilot check
cc-copilot status
```

Interactive `/diff` is available inside Cockpit and `cc-copilot chat`.

LLM-backed answers receive bounded cited evidence context, not tool access or
ambient repo access.

## Cockpit Sessions

A Cockpit Session is separate from an agent session.

It stores:

- your supervision Q&A
- backend/model selection
- project cwd
- selected evidence sessions
- durable compacted memory for older Q&A

Changing evidence with `/sessions` changes what the current Cockpit reads; it
does not switch to another Cockpit Session.

Saved state lives under:

```text
${CC_COPILOT_STATE_DIR:-~/.local/state/cc-copilot}
```

Directories are `0700`, files are `0600`. Disable persistence with:

```bash
cc-copilot cockpit --no-persist
```

or:

```toml
[history]
enabled = false
```

## How It Works

```text
sources/        agent adapters: discover sessions + parse a transcript
  base.py         the AgentSource contract + registry/dispatch
  claude.py       Claude Code (~/.claude/projects/**)
  codex.py        Codex (~/.codex/sessions/**/rollout-*.jsonl)
transcript.py   the normalized record model Claude Code parses into
state.py        fold records into deterministic session state
assess.py       classify stalls, failures, retry loops, and safety signals
brief.py        render deterministic cited recaps
since.py        "what changed since you last looked" (cited diff)
lastlook.py     remember where the human last looked (per session)
handoff.py      a shareable Markdown handoff artifact
notify.py       conservative away-alerts (desktop / terminal)
observe.py      rank attention and next human decision
scope.py        collect session, multi-session, and project evidence
context.py      retrieve raw evidence for model-backed answers
store.py        persist resumable Cockpit Sessions and compacted memory
backends.py     call Codex, Claude, OpenAI-compatible APIs, Ollama, etc.
tui.py          Cockpit, the Textual TUI
```

Agent specifics live entirely in `sources/`. Each adapter supplies just two
things — discovery and parse — and emits the same normalized records, so state,
assessment, briefing, context, and cockpit are agent-agnostic. One cockpit can
watch Claude Code and Codex sessions side by side, grouped by project. Adding
another agent (Gemini CLI, Aider, …) is a new `sources/` adapter, not a rewrite.

## Development

```bash
git clone https://github.com/Audiofool934/cc-copilot.git
cd cc-copilot
pip install -e ".[tui]"          # editable install with the Cockpit extra

python3 -m unittest discover     # stdlib-only test suite
cc-copilot cockpit
```

Core tests are stdlib-only. Textual is optional and lazy-imported by Cockpit.
Releases publish to PyPI automatically on a `v*` tag — see
[`docs/RELEASING.md`](docs/RELEASING.md).

## Roadmap

- Homebrew tap + a `curl | sh` one-line installer
- deeper project evidence retrieval and file ranking
- additional transcript parsers beyond Claude Code
- hook-driven push alerts for unattended runs

Rust migration is tracked separately in [docs/rust-migration.md](docs/rust-migration.md).

## Philosophy

The main agent conversation should stay focused on doing the work.

Supervision is a different job: inspect what happened, compare evidence, ask
what matters, preserve decisions, and decide whether to intervene. Mixing that
into the working agent thread creates noise and changes the very context you
are trying to observe.

CC-Copilot keeps supervision outside the main workflow. Cockpit is where the
human can regain situational awareness without contaminating the agent's own
conversation.
