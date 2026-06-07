# 🛰 cc-copilot

A **read-only "shadow-memory" sidecar** for long-running coding agents. You step
away or switch projects; the agent keeps working. When you come back,
`cc-copilot` tells you — **faithfully** — what it did, whether it's stuck, and
what to look at, without scrolling the transcript.

![cc-copilot cockpit](docs/cockpit.png)

*The cockpit (`cc-copilot cockpit`): a live agent-timeline above your chat, a
safety verdict pill, and answers grounded in evidence — every `[L…]` is a real
transcript line.*

```
$ cc-copilot brief
# 🛰  cc-copilot brief — wire up the SSH reconnect path
`/Users/you/cmux`  branch `ssh-reconnect`  cc v2.1.165
session `b5c53c29…`  ·  5347 events  ·  span 4h

## Status: 🔴 STALLED — no closing message after its last action (interrupted or stuck)
· last activity 22m ago  ·  permission-mode `auto`

## Safety: 🔴 INTERVENE — looks stuck or off-track; don't let it keep running blind
- 🔴 stopped mid-action ~22m ago with no closing message  [L5345]
- 🟠 ran the same command 4× — possible retry loop  [L4497 L4543 L4756 L5239]

## What it's working on (your asks)
- /codex:review --background --base main  [L5301 18:41]
- the reconnect should back off, not hammer a dead host  [L4890 02:10]

## What it did
- tools: Bash×383, Read×129, Edit×83, Grep×65 …
- changed 7 file(s):
    - `Sources/WorkspaceRemoteConfiguration.swift` (4 edits)  [L5235 08:48]
    …
## ⚠ Friction — 23 error result(s)
- Edit failed: File has been modified since read…  [L5230] (call [L5229])
…
```

Every `[L…]` is a line in the session transcript. **cc-copilot states nothing
it can't cite.**

---

## Why this exists

A long-running agent and the human supervising it live on two different clocks.
The agent does an hour of work in a burst; the human is off in another project.
When the human returns, the context is gone — and scrolling a 5,000-line
transcript is not "situational awareness."

The tools that exist today (mid-2026) split into three camps, and a
[deep competitive scan](#landscape) found **none of them** do the thing you
actually want when you walk back to a running agent:

1. **Orchestration terminals** (Conductor, Vibe Kanban, Sculptor, Claude Code's
   own *Agent View*) — run agents and show **status + diffs**. Coarse routing,
   no narrative, no judgment.
2. **Observability platforms** (LangSmith, Langfuse, AgentOps) — **developer
   debugging** over trace corpora. Not a returning-operator briefing.
3. **Async coding agents** (Devin, Cursor, Copilot, Jules) — **watch only
   themselves**, surface author-side PR/diff review.

The open gap — and what cc-copilot aims at — is the **interpretation layer** on
top of the (now-commoditized) transcript data: a recap + a *"is it safe to
continue / did it go off track"* judgment + an observer you can interrogate,
delivered **read-only** and **agent-agnostic**. This MVP is the first leg:
a deterministic, evidence-cited recap.

## The one rule: faithfulness

The failure mode for a tool like this is to become *a second hallucinating
agent* — to confidently narrate things that didn't happen. cc-copilot is built
so it **cannot**: the core is deterministic and every claim carries the
transcript line it came from. The narration layer (LLM prose) is optional and
sits *on top of* the cited facts, never replacing them.

This is enforced, not aspirational — see [Verification](#verification).

## Install / run

No dependencies, Python 3.8+. Nothing to install:

```bash
git clone <this repo> && cd cc-copilot
./cc-copilot brief            # recap the most recent session for $PWD
```

Or put it on your PATH: `ln -s "$PWD/cc-copilot" ~/bin/cc-copilot`.

## Usage

```bash
cc-copilot cockpit                  # ⭐ full-screen TUI cockpit (needs cc-copilot[tui])
cc-copilot chat                     # the same, as a plain zero-dep REPL (chat --tui = cockpit)
cc-copilot status                   # fleet overview: ALL sessions, neediest first
cc-copilot sessions                 # list this project's sessions, newest first
cc-copilot history                  # saved copilot conversations (--all = every project)
cc-copilot brief                    # one-shot recap (defaults to most recent session)
cc-copilot check                    # just the "is it safe to continue?" verdict
cc-copilot ask "did it drift?"      # one-shot grounded Q&A over the session state
cc-copilot brief --narrate          # recap + an LLM narration of the cited facts
cc-copilot brief --latest           # …explicitly the newest
cc-copilot brief <session-id|path>  # a specific session
cc-copilot brief --cwd /path/to/proj
cc-copilot state --json             # the raw state model + assessment (machine-readable)
cc-copilot watch                    # re-render the brief as the transcript grows
```

It resolves sessions from `~/.claude/projects/<encoded-cwd>/<session>.jsonl`,
and by default reports on the most recent session *other than the current one*
(so running `brief` from inside a live session targets the agent you want to
watch — set `$CLAUDE_SESSION_ID` to make that exclusion exact).

## Status semantics

| Status | Meaning |
|---|---|
| 🟢 **RUNNING** | tail is a tool call/result, recently — agent mid-turn |
| 🔴 **STALLED** | mid-turn but quiet > 3 min — interrupted or stuck (the signal you most want) |
| 🟡 **AWAITING AGENT** | you spoke last; it hasn't replied |
| ⚪ **IDLE** | agent gave a closing message — your move |

The distinction between RUNNING and STALLED is the heart of "is it safe to leave
it alone" — a transcript ending on a tool result means the agent had *not*
finished its turn; if that was 30 seconds ago it's working, if it was 30 minutes
ago it died mid-action.

## Safety check — leg ② (`cc-copilot check`)

The differentiated part: not *what* it did, but *how it's going*. A deterministic
pass over the state flags **friction**, each signal cited:

- **stalled** — stopped mid-action with no closing message
- **fail-streak** — N commands failed in a row (flailing)
- **recent failures** — several of the last few commands failed
- **edit-thrash** — repeated failed edits to one file (edit/read race)
- **retry-loop** — the exact same command run many times
- **failing tests** — a test command came back non-zero

Signals are recency-weighted: friction near the tail is live; friction the agent
already recovered from is tagged `_(earlier)_`. The verdict is run-state aware —
**INTERVENE** ("it's running *now* and going wrong") only fires for an *active*
session; a finished session with past friction is **REVIEW**, never intervene.

| verdict | exit | meaning |
|---|:--:|---|
| 🔴 **INTERVENE** | 2 | running/stalled with a live alarm — step in |
| 🟠 **REVIEW** | 1 | friction worth a look before continuing |
| 🟢 **CLEAR** / ⚪ IDLE / 🟡 AWAITING / ∅ empty | 0 | no friction signals |

The exit code makes it scriptable — wire `cc-copilot check` into a `Stop` hook to
get pinged only when an agent actually needs you. It's heuristic by design:
cc-copilot flags friction; you make the call.

## Multiple sessions

Running several agents at once (e.g. parallel CMUX workspaces)? Two ways to work
across them:

```bash
cc-copilot status                 # one screen: every session's status + safety,
cc-copilot status --cwd ~/cmux    #   sorted so whatever needs you is on top
cc-copilot fleet --all            #   (alias; --all = every session, not just recent 10)
```
```
cc-copilot status — /Users/you/cmux  (10 of 14 sessions)
 🔴 stalled    intervene   8m ago   612ev  a1b2c3d4  stopped mid-action [L611]
 🟡 awaiting-agent review  2m ago  1840ev  b5c53c29  3 commands failed in a row [L244]
 🟢 running    clear      12s ago   930ev  9f0e1d2c  add the rollback step
 ⚪ idle       idle        1h ago   240ev  77aa88bb  refactor done
```

And inside a chat you can hop between them live:

```
you> /sessions           # list the project's sessions, numbered
you> /use 2              # switch to #2 (clears chat context for the new session)
you> /use b5c53c29       # …or by id / prefix
```

Pick a session for any one-shot command with a positional id/prefix/path
(`cc-copilot brief <id>`), or `--session`. The deterministic core means `status`
needs no LLM — it's a faithful, friction-ranked board of your whole fleet.

## Cockpit TUI — `cc-copilot cockpit`

A full-screen cockpit (Textual) — the Python analog of Codex's `ratatui` loop and
Claude Code's Ink UI: a **reactive header** (status · safety verdict · backend:model
· idle), a **scrolling log** that interleaves window-1's live timeline with your
chat, a **background watcher** that pushes stall/off-track alerts as the agent
works, and **off-thread backend turns** so it never freezes. Default backend is
**codex** (ChatGPT OAuth); `/model <name>` swaps it live. Click anywhere to focus
the composer, which takes full multilingual input (CJK / emoji); `Shift+Enter`
(or `Ctrl+J`) inserts a newline, `Enter` sends.

```bash
cc-copilot cockpit            # just run it — first launch auto-installs the TUI
                              #   extra into a local .venv, then opens the cockpit
# (explicit / CI:  cc-copilot setup   ·   or   pip install 'cc-copilot[tui]')
# (= cc-copilot chat --tui)
```

In-cockpit: `Enter` send · `/help` · `/brief` `/check` `/diff` (LLM-free) ·
`/sessions` `/use <n|id>` · `/history` (Ctrl+H) · `/model <name>` · `Ctrl+R` refresh ·
`Ctrl+L` clear · `Ctrl+C` quit. Citations (`[L…]`) are preserved verbatim in the log
— the faithfulness guarantee holds in the TUI exactly as in the CLI.

**Your chat persists.** Each conversation is saved locally, keyed to the observed
session, so switching sessions (or relaunching) restores its prior dialogue instead
of losing it. `Ctrl+H` / `/history` browses and re-opens past conversations — even if
the underlying transcript is gone (read-only view). It's stored under
`$CC_COPILOT_STATE_DIR` (default `~/.local/state/cc-copilot`, never under `~/.claude`),
dirs `0700` / files `0600`; opt out with `--no-persist`, `[history] enabled = false`,
or `CC_COPILOT_HISTORY=0`.

The core and the plain `cc-copilot chat` REPL stay **zero-dependency**; Textual is
lazy-imported only by the cockpit, and `python -c "import cccopilot.cli"` works on
a stdlib-only interpreter.

## Chat sidecar — `cc-copilot chat` (the plain REPL)

The main way to use it. In a second terminal, pin to the agent's session and
hold an ongoing read-only conversation while it works:

```bash
cd ~/the-project          # same dir the agent is working in
cc-copilot chat           # pins to that project's most-recent OTHER session
```

```
🛰  cc-copilot chat — attached to b5c53c29….jsonl
[🟢 running · idle 12s · 1840 ev · safety: review]
ask a question, or /help.  Ctrl-D to exit.

you> what was it doing, and did it hit trouble?
[🟢 running · idle 3s · 1843 ev · safety: review]
cc > It's wiring the SSH-reconnect backoff. Hit a 3-command fail-streak
     on the migration [L244 L248 L250] but recovered; tests green [L312].

🔔 window-1 → STALLED · 1 new error(s), e.g. Bash [L1871]
you> is it safe to let it keep going?
…
```

- **Live timeline** — every turn re-parses the (growing) JSONL, so answers never
  lag window-1; a status banner shows it moving.
- **Multi-turn** — it remembers the conversation; follow-ups resolve against both
  the prior answers and the just-refreshed state.
- **Push alerts** — a background thread pings you inline when the agent stalls /
  goes off-track / errors (`--no-alerts` to silence; `--poll N` to tune).
- **Read-only by construction** — the only file op is `open(path,'r')`; verified
  byte-identical before/after. It cannot touch the agent it watches.
- **LLM-free escape hatches** — `/brief`, `/check`, `/diff`, `/refresh`,
  `/session`, `/history` work without a backend, and let you verify any prose
  answer against cited evidence in the same window.

Grounding is identical to `ask`: the chat LLM sees only the cited brief, and
prior turns are replayed as *already-grounded* answers — never as new facts — so
a long conversation can't launder an un-cited claim into a later reply.

## Observer chat — leg ③ (`cc-copilot ask`, `brief --narrate`)

The conversational layer — *grounded* so it can't become a second hallucinating
agent. The LLM never sees the raw transcript; it sees the **deterministic,
evidence-cited brief** from legs ①/② and is told to answer only from it, keeping
the `[L…]` citations:

```bash
cc-copilot ask "did it go off-track, and is it safe to continue?"
cc-copilot ask "draft a one-line next instruction"
cc-copilot brief --narrate          # recap + a 3–5 sentence grounded narration
```

Because it's pinned to the cited facts, it answers with citations and **declines
to guess** — e.g. it won't suggest "commit" when the brief shows no git state.
The narration is the one non-deterministic layer and is labelled as such; legs
①/② remain the deterministic ground truth beneath it.

The model is pluggable — see **Models / backends** below.

## Models / backends

The deterministic core (`brief`, `check`, the chat's live refresh + alerts) uses
**no model at all**. Only the language features (`ask`, `chat`, `--narrate`)
call an LLM, and the backend is your choice:

```bash
cc-copilot backends                       # list backends + availability
cc-copilot chat --backend codex           # or deepseek / ollama / openai / …
cc-copilot ask "…" --backend deepseek --model deepseek-reasoner
export CC_COPILOT_BACKEND=codex           # set a default
```

| backend | how it authenticates | notes |
|---|---|---|
| `claude` *(default)* | your Claude Code login | `claude -p`; no API key |
| `codex` | your `codex login` (ChatGPT **OAuth**) | `codex exec`; agentic CLI |
| `gemini` / `llm` | the CLI's own config | if installed on PATH |
| `deepseek` | `DEEPSEEK_API_KEY` | OpenAI-compatible HTTP |
| `openai` | `OPENAI_API_KEY` | |
| `openrouter` | `OPENROUTER_API_KEY` | any model on OpenRouter |
| `ollama` | none (local) | `http://localhost:11434`; set `--model` |

Two escape hatches for anything else (both zero-dep):
- **Any OpenAI-compatible API** — `CC_COPILOT_API_BASE` (+ `CC_COPILOT_API_KEY`,
  `CC_COPILOT_MODEL`). Points at vLLM, LM Studio, Together, Groq, a proxy, etc.
- **Any CLI** — `CC_COPILOT_LLM_CMD` (e.g. `"llm -m gpt-4o"`); the prompt is
  appended as the final argument.

Grounding is identical across all of them: every backend receives only the cited
brief with the no-invention preamble. If the selected backend is unavailable,
`--narrate` degrades to the plain (LLM-free) brief and `ask`/`chat` say so.

### Set defaults once — `~/.cc-copilot.toml`

```bash
cc-copilot config --init     # write a starter file (chmod 600)
cc-copilot config            # show the path + effective backend
```

```toml
# ~/.cc-copilot.toml
backend = "codex"               # default backend
model   = "deepseek-reasoner"   # default model (optional)

[env]                           # exported as env vars (real env still wins)
DEEPSEEK_API_KEY = "sk-…"
CC_COPILOT_API_BASE = "http://localhost:11434"
```

Precedence everywhere: **explicit `--backend`/`--model` flag > real env var >
this file > built-in default.** Keys live in the `[env]` table (kept `chmod 600`);
point `$CC_COPILOT_CONFIG` elsewhere to relocate it.

## How it works

```
transcript.py   parse the JSONL ledger into normalized, line-addressed records
                (filters harness-injected isMeta / isCompactSummary / <synthetic>
                 text; recovers genuine /slash-command invocations)
state.py        fold records into a deterministic working-state model — plan,
                changed files (failed edits excluded), commands, errors, status —
                each fact carrying its evidence line(s)
brief.py        render the evidence-cited recap
locate.py       map cwd ⇄ ~/.claude/projects session files
cli.py          sessions / brief / state / watch
```

The data plane (parse JSONL + hooks) is deliberately thin and replaceable —
several other tools already do it. cc-copilot's value is the **reading** of that
data, not its collection.

## <a name="verification"></a>Verification

The faithfulness claim is checked adversarially, not asserted. A fleet of
auditor agents each take a *real* session, run `brief`, then re-read every cited
JSONL line and try to find a claim the line doesn't support. (The audit harness
is a reusable workflow — re-run it as a regression gate.)

Three rounds against 10 real sessions, ~150 citations re-checked each time —
**zero fabrications in any round** (every cited line always existed with
matching text); the bugs were misattribution/classification, and each round
drove them down:

| round | unfaithful files | critical | major | minor |
|------:|:---:|:---:|:---:|:---:|
| 1 | 6/10 | 1 | 11 | 2 |
| 2 | 1/10 | 0 | 1 | 1 |
| **3** | **0/10** | **0** | **0** | **1** (wording) |

Bugs found and fixed:

- **status lied mid-turn** — a transcript ending on a tool result was reported
  IDLE ("finished, waiting on you") instead of RUNNING/STALLED;
- **harness text laundered as human/agent words** — `isMeta` resume stubs,
  slash-command template bodies, `isCompactSummary` summaries, and `<synthetic>`
  placeholders leaked into "your asks" / "agent's last words";
- **failed edits counted as changes** — an `Edit` whose result was `is_error`
  still inflated the changed-file count;
- **housekeeping commands as asks** — `/compact`, `/clear`, `/mcp` etc. were
  surfaced as intents and (when trailing) mis-anchored the status.

## Roadmap

The differentiated product is the full trio:

- **① recap** — ✅ evidence-cited "what it did while you were away" (`brief`)
- **② judgment** — ✅ "is it safe to continue / did it go off track" (`check`:
  fail-streaks, edit-thrash, retry-loops, stalls, failing tests — recency-weighted)
- **③ observer chat** — ✅ ask it "did it drift?", "draft the next instruction"
  (`ask` / `--narrate`) — an LLM layer *grounded in the cited state*

The trio the [landscape](#landscape) said was the open gap is now built end to
end. Next: hook-driven push ("it stalled 10 min ago" — `check`'s exit code
already supports this), a live `watch` + narrate loop, and other agents (Codex,
Gemini CLI) behind the same `State` model (only the `transcript.py` parser is
Claude-Code-specific; `state`/`assess`/`brief`/`narrate` are agent-agnostic).

## Development

```bash
git clone <repo> && cd cc-copilot
./cc-copilot brief --cwd ~/some-project      # runs on stdlib alone

python3 -m unittest discover -s tests        # 43 tests, no deps
cc-copilot setup                             # adds the optional TUI (.venv + textual)
```

Layout: the core (`transcript` → `state` → `assess` → `brief`) is pure,
deterministic, and dependency-free; `narrate`/`backends` add the optional LLM
layer; `chat`/`tui` are the interactive surfaces; `cli` ties it together. Only
`transcript.py` is Claude-Code-specific — everything downstream is agent-agnostic.
Tests stay stdlib-only; Textual is an optional extra, lazy-imported by the cockpit.

## <a name="landscape"></a>Landscape (mid-2026)

Closest contenders and why they don't close the gap: **Devin** Session Insights
(recap, but single-vendor, no open-ended safety verdict, no observer chat);
**GitHub Copilot** PR summary + self-review (PR-bound, single-vendor); **Cursor**
(diff-scoped review; drift detection is a *separate* security product);
**Claude Code Agent View** (multi-session status board, explicitly avoids
transcripts). Research precedent for "agent-watching-agent" exists (Meta **Wink**,
Scale **MRT**, **InferAct**) but emits machine-consumed corrections/scores, not a
human-facing recap. The recap leg is being encroached; the **judgment + observer**
legs are open.
