# cc-copilot Agent Observability Strategy

Date: 2026-06-07

## Purpose

cc-copilot should not become another coding agent. The meaningful product is an
agent observability cockpit: a read-only, real-time compression layer that helps
a human regain situational awareness when agents produce more output than the
human can process.

The winning question is not "can we automate more?" It is:

> What does the human need to know right now to safely and confidently re-enter
> the workflow?

This document captures research and product direction for cc-copilot as an
agent observability and human bandwidth management tool.

## Core Mission

In automation scenarios, especially long-running agent tasks, the human's
cognitive bandwidth cannot keep up with the agent's raw output. The transcript
may contain thousands of tool calls, edits, failed commands, retries, summaries,
and status changes. The human does not need all of it at once. The human needs
the right compression at the right moment.

cc-copilot should answer four questions faster than a human can by reading raw
logs:

1. What is happening now?
2. What changed since I last looked?
3. Is anything stuck, risky, looping, or waiting for me?
4. What is the smallest next human decision?

## Product Category

The category is not "AI coding assistant." It is:

**Agent observability + human bandwidth management.**

This means:

- Read-only by default.
- Evidence-first, with citations into original sources.
- Real-time enough to support active supervision.
- Focused on attention, safety, status, drift, and re-entry.
- Agent-agnostic over time.
- Useful before, during, and after long-running work.

The strategic wedge is trust. Every major agent product is trying to do more.
cc-copilot should help the human understand more.

## Industry Pattern

The industry is moving from "chat with one agent" toward multi-surface,
long-running, inspectable agent work. The leading products increasingly share a
few traits:

- Terminal, editor, desktop, browser, and cloud surfaces.
- Durable sessions or threads.
- Multi-agent or background-agent execution.
- Session search, resume, fork, and share.
- Permission and approval controls.
- Tool/event traces.
- MCP or adjacent extension protocols.
- Git, diff, worktree, and PR workflows.
- Notifications and attention mechanisms.
- Human-in-the-loop checkpoints.

cc-copilot should not imitate all of this. It should observe and compress it.

## Product References

### Codex

Codex is the strongest reference for terminal-first control and local developer
workflow. It emphasizes a full-screen TUI, plan and approval loops, diffs,
session resume, slash commands, configurable status lines, worktrees, subagents,
cloud tasks, and app-side task sidebars.

Design philosophy:

- Keep the agent powerful.
- Make state, permissions, and review visible.
- Preserve threads and plans.
- Support many surfaces while sharing configuration and context.

Lessons for cc-copilot:

- The status surface matters as much as the chat surface.
- Slash commands are a strong control grammar.
- Resume, fork, review, and diff are core re-entry workflows.
- A cockpit should expose model, scope, branch, context, and task progress at a
  glance.
- Worktrees and parallel threads increase the need for an external observer.

Primary source:

- [OpenAI Codex manual](https://developers.openai.com/codex/codex-manual.md)

### OpenCode

OpenCode is the clearest terminal-native design reference. It markets a
responsive, native, themeable TUI, LSP support, multi-session support,
shareable session links, and broad model/provider support. Its TUI docs also
show strong keyboard-first command design, configurable keybinds, mouse
settings, attention notifications, scroll behavior, external editor support,
session switching, sharing, undo/redo, themes, and command palette patterns.

Design philosophy:

- TUI as primary product surface.
- Sessions as first-class objects.
- Configuration belongs to the terminal user.
- Attention mechanisms should be tunable.
- Remote attach and web/mobile access matter.

Lessons for cc-copilot:

- We should treat multi-session status as a native surface, not a feature
  hidden behind chat.
- Attention settings should become explicit product controls.
- Session links and exports are powerful for debugging and collaboration.
- Themeability and keyboard ergonomics are not polish; they are retention.

Primary sources:

- [OpenCode homepage](https://thdxr.dev.opencode.ai/)
- [OpenCode TUI docs](https://open-code.ai/en/docs/tui)
- [OpenCode CLI docs](https://open-code.ai/en/docs/cli)

### Claude Code

Claude Code is the strongest reference for lifecycle integration and
extensibility. It spans terminal, IDE, desktop, web, and mobile handoff. It
supports MCP, project guidance, skills, hooks, background agents, subagents,
scheduling, and session movement between surfaces.

Its hooks are especially important for cc-copilot. Hook inputs expose fields
like transcript path, current working directory, permission mode, subagent
transcript path, last assistant message, background tasks, and session crons.
This validates the core cc-copilot premise: the transcript and lifecycle event
stream are enough to build meaningful observation.

Design philosophy:

- Make the agent deeply extensible.
- Let teams encode policy and workflows.
- Let sessions move across devices and surfaces.
- Support background and scheduled work.
- Use hooks for deterministic lifecycle intervention.

Lessons for cc-copilot:

- Transcript observation is a durable primitive.
- We should eventually support hook/event ingestion where available.
- Subagents and background tasks need parent/child visualization.
- "Waiting for human input" is a first-class state.
- Lifecycle events are more valuable than raw prose.

Primary sources:

- [Claude Code overview](https://code.claude.com/docs/en/overview)
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)

### Amp

Amp is the strongest "thread as durable knowledge object" reference. Threads
can be shared, searched, referenced from future work, and added to commits.
It also supports CLI/IDE integration, agent modes, thread mentions, plugins,
subagents, and command palette workflows.

Design philosophy:

- Threads are reusable artifacts.
- Past agent work is organizational memory.
- Opinionated product constraints can be a feature.
- The command palette is the main control surface.

Lessons for cc-copilot:

- Session history should become searchable project memory.
- A future "related sessions" view could be a major differentiator.
- Handoff summaries should be durable and linkable.
- "What did similar agents do last time?" is a valuable re-entry question.

Primary source:

- [Amp manual](https://ampcode.com/manual)

### Gemini CLI

Gemini CLI validates the simple, open-source terminal agent pattern. It uses a
ReAct loop with built-in tools and local or remote MCP servers, and its core is
also reused in Gemini Code Assist agent mode.

Design philosophy:

- Terminal agents can be general local utilities, not only coding tools.
- Open-source distribution increases ecosystem adoption.
- MCP is becoming table stakes for agent extensibility.

Lessons for cc-copilot:

- We should stay adapter-friendly, not Claude-only.
- MCP-like interoperability may matter more than bespoke integrations over
  time.
- Terminal-first tools can still power IDE-facing experiences.

Primary source:

- [Google Gemini CLI docs](https://developers.google.com/gemini-code-assist/docs/gemini-cli)

### Aider

Aider remains the small, predictable, git-native baseline: terminal pair
programming, local git repository edits, in-chat commands, chat modes, model
choice, notifications, watch/IDE integrations, linting, testing, and broad
provider support.

Design philosophy:

- Stay close to git and the shell.
- Be predictable and scriptable.
- Let users choose models and workflows.

Lessons for cc-copilot:

- A small tool can win by being reliable and clear.
- Git-native facts should remain central.
- Provider flexibility matters, but not more than product clarity.

Primary source:

- [Aider docs](https://aider.chat/docs/)

### Cursor

Cursor's background agents represent the async cloud-agent direction. Agents
run in isolated remote machines, work on separate branches, push to GitHub, and
can be viewed or followed up from the editor.

Design philosophy:

- Background work should be visible and resumable.
- Remote environments make long-running tasks easier to offload.
- GitHub integration is the handoff path.

Lessons for cc-copilot:

- Background agents create more need for observability, not less.
- Project-level status should distinguish local, remote, branch, and PR state.
- Security posture matters when agents auto-run terminal commands.

Primary sources:

- [Cursor Background Agents](https://docs.cursor.com/background-agent)
- [Cursor CLI](https://cursor.com/cli/)

### Windsurf Cascade

Windsurf Cascade is an editor-native reference for real-time awareness,
terminal integration, queued messages, checkpoints, MCP, command approval
levels, and allow/deny lists.

Design philosophy:

- The agent should understand editor and terminal context.
- Users need explicit command execution controls.
- Queued messages help humans keep thinking while agents work.

Lessons for cc-copilot:

- Terminal output is context, not noise.
- Queueing future questions or follow-ups could be useful.
- Approval state and command policy should be observable facts.

Primary sources:

- [Windsurf Cascade overview](https://docs.windsurf.com/windsurf/cascade)
- [Windsurf Terminal docs](https://docs.windsurf.com/windsurf/terminal)

### Goose

Goose is a local general-purpose agent with desktop, CLI, API, mobile access,
and MCP-native extension architecture. It emphasizes local execution,
extensibility, and sessions that can be accessed across surfaces.

Design philosophy:

- General local agent, not only coding.
- MCP extensions are the main extensibility story.
- Same sessions should be reachable from desktop, CLI, IDE, and mobile.

Lessons for cc-copilot:

- Multi-surface observation may eventually matter.
- Local-first and open-source positioning are compatible with extensibility.
- Session continuity across surfaces is becoming a norm.

Primary sources:

- [Goose homepage](https://block.github.io/goose/)
- [Goose CLI docs](https://block.github.io/goose/docs/guides/goose-cli-commands)
- [Goose mobile and terminal support](https://block.github.io/goose/blog/2025/12/19/goose-mobile-terminal)

## Agent Architecture Research

### Workflows vs Agents

Anthropic's "Building Effective Agents" makes the most useful distinction:

- Workflows orchestrate LLMs and tools through predefined code paths.
- Agents dynamically direct their own process and tool usage.

cc-copilot should mostly be a workflow, not an agent. Our core job is
deterministic:

1. Observe.
2. Parse.
3. Classify.
4. Compress.
5. Alert.
6. Cite.

LLM narration should be optional and downstream of deterministic evidence.

Primary source:

- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

### Observability and Tracing

The OpenAI Agents SDK treats tracing as central: traces can include model
generations, tool calls, handoffs, guardrails, and custom events. This is the
same shape cc-copilot should want from every coding agent, even if we currently
derive it from transcripts.

Important concepts:

- Trace: one end-to-end operation or workflow.
- Span: timed operation within a trace.
- Group ID: link related traces to a conversation/thread.
- Sensitive data controls.
- Tool execution controls.
- Guardrails and approval flows.

Primary sources:

- [OpenAI Agents SDK guide](https://developers.openai.com/api/docs/guides/agents)
- [OpenAI Agents SDK tracing docs](https://github.com/openai/openai-agents-python/blob/main/docs/tracing.md)
- [OpenAI Agents SDK running agents](https://openai.github.io/openai-agents-python/running_agents/)
- [OpenAI Agents SDK guardrails](https://openai.github.io/openai-agents-python/guardrails/)

### Durable Execution and Human-in-the-loop

LangGraph's positioning maps closely to long-running agent supervision:
durable execution, streaming, human-in-the-loop, persistence, memory, and trace
visualization.

cc-copilot does not need to adopt LangGraph, but it should borrow the product
language:

- Durable state.
- Resumable observation.
- Streaming updates.
- Human inspection and intervention.
- Trace-driven debugging.

Primary source:

- [LangGraph overview](https://docs.langchain.com/langgraph)

### Agents vs Workflows in Production

Microsoft Agent Framework gives a useful product rule:

- Use agents when the task is open-ended, conversational, or requires
  autonomous planning and tool use.
- Use workflows when the process has well-defined steps or explicit execution
  order.

cc-copilot's observer core has well-defined steps, so it should remain a
workflow. The product may use agents for explanation, research, or synthesis,
but not for the basic truth layer.

Primary source:

- [Microsoft Agent Framework overview](https://learn.microsoft.com/en-gb/agent-framework/overview/)

## TUI and Platform Design

### Current Choice: Python + Textual

Python and Textual remain right for cc-copilot's current stage:

- Fast product iteration.
- Simple local file parsing.
- Good testability.
- Existing codebase fit.
- Rich terminal UI primitives.
- Potential web rendering path through Textual's ecosystem.

Tradeoffs:

- Packaging is less frictionless than a single static binary.
- Very high-frequency or very large dashboards may eventually stress Python.
- Terminal edge cases still require care across macOS, Linux, WSL, Windows,
  and different terminal emulators.

Primary source:

- [Textual GitHub](https://github.com/textualize/textual)

### Rust + Ratatui

Ratatui is strong for fast, lightweight, native terminal dashboards.

Potential future fit:

- If the TUI becomes performance-bound.
- If single-binary distribution becomes critical.
- If we want a long-lived real-time dashboard with very low overhead.

Primary source:

- [Ratatui](https://ratatui.rs/)

### Go + Bubble Tea

Bubble Tea is strong for production terminal apps with a functional update loop,
cell renderer, keyboard/mouse handling, and simple static binaries.

Potential future fit:

- If distribution simplicity becomes the top constraint.
- If we want one binary across macOS, Linux, and Windows.
- If we want a small event-loop architecture around adapters and views.

Primary source:

- [Bubble Tea](https://github.com/charmbracelet/bubbletea)

### TypeScript + Ink

Ink is attractive for teams already in React/TypeScript, and several agent CLIs
use or resemble the React-in-terminal approach.

Potential future fit:

- If integrations with Node-based agent ecosystems dominate.
- If we want React-like component patterns for the terminal.

Primary source:

- [Ink](https://github.com/vadimdemedes/ink)

## Design Principles for cc-copilot

### 1. Read-only trust is the product moat

cc-copilot should never need write access to the agent's workspace to be useful.
Its job is to interpret, not mutate.

### 2. Deterministic facts before LLM narration

The evidence model should be deterministic, citeable, and testable. LLMs can
explain the evidence, but should not invent the evidence.

### 3. Attention is a scarce resource

The product should avoid alert fatigue as carefully as it avoids missing
failures.

Suggested attention tiers:

- Silent: low-value ongoing activity.
- FYI: notable but not urgent.
- Review soon: friction or uncertainty.
- Intervene now: stuck, looping, risky, or waiting for human input.

### 4. Re-entry beats raw monitoring

The most important workflow is not watching every event. It is returning after
time away and quickly understanding the state of the work.

Key flows:

- Since I last looked.
- What changed in the last N minutes.
- Why did you alert me?
- What needs my decision?
- What files/tests/commands matter?
- What is the smallest safe next step?

### 5. Multi-session is a first-class surface

Multi-agent and background-agent workflows are becoming normal. cc-copilot
should treat a project as a board of sessions, not as a single transcript.

### 6. Citations are the trust loop

Every summary should support expansion into evidence:

Short answer -> evidence row -> transcript/file/git citation.

### 7. Adapters, not rewrites

Claude Code may be the first strong adapter, but the event model should allow
Codex, OpenCode, Gemini CLI, Aider, Amp, Goose, Cursor, and Windsurf sources
over time.

## Recommended Product Direction

### Build an Agent Event Core

Normalize agent outputs into one event model:

- User prompt.
- Assistant message.
- Tool call.
- Tool result.
- File edit/write.
- Git change.
- Command run.
- Test run.
- Failure.
- Permission wait.
- Human-input wait.
- Session title.
- Session branch.
- Current working directory.
- Subagent start/stop.
- Background task start/stop.
- Alert.
- Summary.
- Citation.

### Move from reparsing to an incremental local index

SQLite is the obvious next spine:

- Transcript path.
- Agent type.
- Session ID.
- Project root.
- Byte offset.
- Parsed events.
- Derived state.
- Session metadata.
- Last-seen timestamp.
- Last-human-look timestamp.
- Attention state.

Polling can be fast if we tail files, parse incrementally, and debounce project
facts.

### Make the TUI a control room

The top-level screen should prioritize:

- Project/scope header.
- Session board.
- Needs-attention lane.
- Recent meaningful activity.
- Changed files.
- Tests/commands.
- Chat/explanation as secondary.

The cockpit should not feel like a generic chat window with a status bar. It
should feel like a readable control room for autonomous work.

### Add return-to-work commands

High-value commands:

- `/since last-look`
- `/since 30m`
- `/why-alert`
- `/needs-me`
- `/risks`
- `/tests`
- `/files`
- `/handoff`
- `/sessions`
- `/project`

### Build adapter boundaries now

Define agent adapters before adding many agent-specific features:

- `AgentSource`: discovers sessions.
- `EventReader`: tails/parses event stream.
- `StateBuilder`: folds events into status.
- `EvidenceRenderer`: produces cited evidence.
- `AttentionEngine`: scores user relevance.

## Proposed Roadmap

### v0.5: Agent Observability Core

Goal: move from transcript rendering to indexed, multi-agent-ready
observability.

Features:

- SQLite event index.
- Incremental transcript tailing.
- Normalized event schema.
- Last-look tracking.
- Session board backed by indexed state.
- Attention scoring v2.
- `/since last-look`.
- Adapter interface, with Claude Code as first implementation.

### v0.6: Multi-agent Project Board

Goal: make project scope a true operating surface.

Features:

- Project dashboard.
- Session grouping by branch/task/status.
- Needs-attention lane.
- Subagent/background task visualization where available.
- Cross-session changed-file aggregation.
- Cross-session test/failure aggregation.

### v0.7: Re-entry and Handoff

Goal: make the product indispensable when returning to long-running work.

Features:

- Handoff summaries.
- "What needs my decision?" workflow.
- Timeline bookmarks.
- Last-look snapshots.
- Exportable project status reports.
- Shareable Markdown session/project briefs.

### v0.8: Agent Adapter Expansion

Goal: prove cc-copilot is agent-agnostic.

Candidate adapters:

- Codex.
- OpenCode.
- Gemini CLI.
- Aider.
- Amp.
- Goose.

### v0.9: Attention and Notification Layer

Goal: make the product useful while the user is away.

Features:

- Desktop notifications.
- Configurable attention rules.
- Quiet hours.
- Watch mode.
- Webhook/Slack/terminal notification targets.
- Alert acknowledgements.

## Risks and Open Questions

### Risk: becoming another agent UI

If chat dominates the product, cc-copilot loses its unique reason to exist.
The chat should explain evidence, not replace the cockpit.

### Risk: summarization without trust

If summaries are not citeable, users will stop trusting them. Evidence links
must remain central.

### Risk: alert fatigue

Too many notifications will make users disable the product. Attention scoring
needs testing and conservative defaults.

### Risk: adapter fragility

Agent transcript formats can change. The adapter interface and tests should
make breakage contained and visible.

### Risk: platform-specific terminal behavior

Mouse, keyboard protocols, IME input, alternate-screen rendering, Unicode,
Windows terminals, and SSH/tmux all need explicit testing.

## Strong Recommendation

Stop thinking of cc-copilot as "a companion chat." Think of it as:

**The missing observability layer for autonomous work.**

The next major milestone should be v0.5: Agent Observability Core.

That milestone moves cc-copilot from a useful TUI into a product with a real
identity:

- read-only
- real-time
- cited
- multi-session
- attention-aware
- agent-agnostic
- built for human re-entry into autonomous workflows

## Source Index

Agent products:

- [OpenAI Codex manual](https://developers.openai.com/codex/codex-manual.md)
- [OpenCode homepage](https://thdxr.dev.opencode.ai/)
- [OpenCode TUI docs](https://open-code.ai/en/docs/tui)
- [OpenCode CLI docs](https://open-code.ai/en/docs/cli)
- [Claude Code overview](https://code.claude.com/docs/en/overview)
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
- [Amp manual](https://ampcode.com/manual)
- [Google Gemini CLI docs](https://developers.google.com/gemini-code-assist/docs/gemini-cli)
- [Aider docs](https://aider.chat/docs/)
- [Cursor Background Agents](https://docs.cursor.com/background-agent)
- [Cursor CLI](https://cursor.com/cli/)
- [Windsurf Cascade overview](https://docs.windsurf.com/windsurf/cascade)
- [Windsurf Terminal docs](https://docs.windsurf.com/windsurf/terminal)
- [Goose homepage](https://block.github.io/goose/)
- [Goose CLI docs](https://block.github.io/goose/docs/guides/goose-cli-commands)
- [Goose mobile and terminal support](https://block.github.io/goose/blog/2025/12/19/goose-mobile-terminal)

Agent architecture:

- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [OpenAI Agents SDK guide](https://developers.openai.com/api/docs/guides/agents)
- [OpenAI Agents SDK tracing docs](https://github.com/openai/openai-agents-python/blob/main/docs/tracing.md)
- [OpenAI Agents SDK running agents](https://openai.github.io/openai-agents-python/running_agents/)
- [OpenAI Agents SDK guardrails](https://openai.github.io/openai-agents-python/guardrails/)
- [LangGraph overview](https://docs.langchain.com/langgraph)
- [Microsoft Agent Framework overview](https://learn.microsoft.com/en-gb/agent-framework/overview/)

TUI/platform:

- [Textual](https://github.com/textualize/textual)
- [Ratatui](https://ratatui.rs/)
- [Bubble Tea](https://github.com/charmbracelet/bubbletea)
- [Ink](https://github.com/vadimdemedes/ink)
