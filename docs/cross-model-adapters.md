# Cross-Model Agent Adapters

Date: 2026-06-08

## Why

cc-copilot started as a Claude Code observer. The strategic next step is to make
it **agent-agnostic**: one read-only cockpit that watches a Claude Code session
*and* a Codex session (and later Gemini CLI, Aider, …) side by side, grouped by
project, with the same evidence-cited recap / safety verdict / observer chat.

The unlock is that every serious terminal coding agent persists a **per-session
append-only JSONL ledger** on disk — the exact primitive cc-copilot already
exploits for Claude Code. Adding an agent is therefore a *format-mapping* job,
not an architecture change.

## The seam

The whole pipeline below `transcript` already speaks one normalized model:

```
discover sessions ─┐
                   ├─►  Transcript{ records: [Record] }  ──►  state.build  ──►  State
parse one session ─┘                                          assess / brief / observe / context
```

`state.build()` and everything downstream consume only `Transcript`/`Record`.
So an adapter needs to supply exactly two things, and nothing else changes:

1. **discovery** — given a project cwd, list that agent's sessions on disk.
2. **parse** — given a session file, produce a `Transcript` of normalized
   `Record`s (`human`, `agent_text`, `agent_thinking`, `tool_call`,
   `tool_result`, `snapshot`, `system`).

That contract is `AgentSource` (`cccopilot/sources/base.py`).

```
cccopilot/sources/
  base.py     AgentSource ABC + registry
  claude.py   ClaudeSource — delegates to locate.py + transcript.py (no behavior change)
  codex.py    CodexSource  — reads ~/.codex/sessions/**/rollout-*.jsonl
  __init__.py dispatch: parse(path), list_sessions(cwd), resolve(...), source_for_path(path)
```

Discovery/parse call sites move from `transcript.parse(p)` / `locate.list_sessions(cwd)`
to the dispatcher `sources.parse(p)` / `sources.list_sessions(cwd)`, which routes
by source. A Claude path still lands in the exact same parser, so the Claude
experience is unchanged and its tests stay green.

## Codex on-disk format (verified 2026-06-08, codex 0.137.0)

```
~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-ts>-<uuid>.jsonl
~/.codex/archived_sessions/rollout-*.jsonl     # older/finished
~/.codex/session_index.jsonl                   # index (cwd, id, …) — optional fast path
```

Each rollout line is an envelope `{timestamp, type, payload}`:

| `type`          | `payload` shape                                              | maps to |
|-----------------|-------------------------------------------------------------|---------|
| `session_meta`  | `{id, timestamp, cwd, model_provider, cli_version, …}`      | session id, project cwd, model |
| `response_item` | `{type: message/reasoning, role, content[]}`                | `human` / `agent_text` / `agent_thinking` / `tool_call` / `tool_result` |
| `event_msg`     | `{type: task_started/task_complete/token_count/agent_message/user_message, …}` | lifecycle (status), `agent_text`, `human` |
| `turn_context`  | `{cwd, model, approval_policy, …}`                          | header / cwd fallback |

Notes:
- `cwd` lives in `session_meta` → project grouping works just like Claude Code's
  encoded-cwd directory, but derived from file contents instead of the path.
- Codex emits `task_started` / `task_complete` natively — cleaner lifecycle
  signal than Claude Code's tail-inference (we still fold to the same `status`).
- `reasoning` items are Codex's thinking; mapped to `agent_thinking`.
- Tool calls in Codex appear as `function_call` / `local_shell_call` response
  items (+ their `*_output` results); mapped to `tool_call` / `tool_result` so
  the existing Bash/Edit folding and failure detection keep working.

## Phasing

- **Phase 1a** — carve the `AgentSource` seam; Claude Code becomes adapter #1 by
  delegation. Pure refactor, behavior-preserving, tests green.
- **Phase 1b** — add `CodexSource`; `list_sessions` unions enabled sources, each
  `SessionRef` tagged with its `agent`; cockpit/status/observe show both agents
  grouped by project. Opt out / filter via `--agent` and `[agents]` config.
- **Phase 2** (separate) — "while you were away" (`/since`, `/handoff`, alerts)
  built on the normalized model, so it works for every adapter for free.

## Safety

Read-only contract is unchanged and now spans agents: cc-copilot never writes
under `~/.claude` *or* `~/.codex`. Adapters only read session files and never
mutate the observed agent.
