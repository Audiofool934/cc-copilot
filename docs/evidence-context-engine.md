# Evidence Context Engine

Status: accepted for v0.7
Date: 2026-06-08

## Core Principle

cc-copilot should not treat summaries or briefs as the Copilot's source of
truth. The source of truth is primary observable context:

```text
raw agent transcript
raw tool calls/results
raw assistant messages
read-only project facts
git/file evidence
full cockpit conversation log
```

Summaries remain useful, but only as navigation aids, indexes, and UI surfaces.
They should never be the only context used for an answer when raw evidence is
available.

The product shift is:

> cc-copilot is not a chatbot over a brief. It is a read-only evidence engine
> with a conversational layer on top.

## Mission Fit

The core problem is human bandwidth. In long-running automation, agents can
produce more output than a human can comfortably inspect in real time.
cc-copilot should help the human understand the workflow as it unfolds by
retrieving the exact evidence that matters, explaining it plainly, and showing
what coverage the answer actually had.

## Source Boundary

cc-copilot can read:

- Claude Code JSONL transcripts under `~/.claude` or `$CLAUDE_CONFIG_DIR`.
- Raw user prompts, assistant messages, tool calls, and tool results recorded in
  those transcripts.
- Session metadata such as cwd, branch, title/name, timestamps, session id,
  permission mode, and version.
- Selected multi-session evidence.
- Bounded read-only project context such as git status, file index, changed
  files, and cited text excerpts.
- cc-copilot's own Cockpit Session conversation log.

cc-copilot cannot read:

- Claude's hidden internal model context.
- Private chain-of-thought.
- Any reasoning/cache that Claude Code did not write to the transcript.
- Unobservable state inside the running agent process.

The promise is complete observable context, not complete hidden Claude context.

## Main Goals

1. **Evidence-first chat**
   Replace the "render evidence recap, then ask model" path with a context
   engine that assembles raw, cited evidence for each question.

2. **Question-aware expansion**
   If the user asks about a specific detail, cc-copilot should pull full
   transcript messages around relevant lines, not rely on shortened recap text.

3. **No fixed round limit**
   Remove `history[-8:]`-style limits. Long cockpit conversations should be
   handled through token budgeting and compaction, not arbitrary turn count.

4. **Codex-like compact behavior**
   Keep the full Cockpit Session on disk, but compact older cockpit Q&A into
   durable memory when needed. Recent turns stay raw; older turns become
   structured memory.

5. **Context HUD**
   Show the user what the Copilot actually has in context: raw transcript,
   project facts, cockpit chat, summary index, output tokens, and total context
   window usage.

## Architecture

```text
Transcript / project / cockpit logs
-> Evidence Index
-> Question-aware Retriever
-> Context Budgeter
-> Model Prompt
-> Answer with citations + coverage note
```

## Evidence Index

Build an internal index over primary records:

- Full assistant messages
- Full human prompts
- Tool calls
- Tool results
- Thinking snippets where available
- File/project facts
- Git status/diff facts
- Session metadata
- Cited line ranges

Each record preserves:

```text
session_id
line number
kind
timestamp
raw text
short label
token estimate
importance score
```

## Question-Aware Retrieval

For each user question, assemble context by tiers:

1. Always include current status/header facts.
2. Always include recent raw transcript tail.
3. Include full raw records around cited/relevant lines.
4. Include matching records by keyword/entity search.
5. Include related tool call/result pairs together.
6. Include project facts only when needed or when budget allows.
7. Include summary/index last, as orientation only.

Example: if the user asks "keeper yield 是多少", the retriever should pull the
full assistant message containing the table, not the 240-character "last words"
summary.

## Context Budgeter

Use a token budget instead of a fixed turn count:

```text
system/preamble
+ cockpit memory summary
+ recent cockpit raw turns
+ fresh raw evidence
+ project facts
+ current user question
<= model context budget
```

Suggested thresholds:

```text
< 60% context: normal
60-80%: HUD warning
80-95%: compact older cockpit turns
> 95%: reduce lower-priority evidence tiers
still too large: clear context-too-large message
```

## Compaction

Compaction should apply mostly to the cockpit conversation, not the observed
agent transcript.

The raw Cockpit Session log stays complete on disk. When needed, older Q&A
becomes structured memory:

```text
decisions made
user preferences
known project facts discussed
open questions
discarded assumptions
important citations
```

Recent cockpit turns stay raw because they carry local conversational nuance.

## HUD Design

Add a Claude/Codex-style status segment:

```text
ctx 82k / 128k · raw 61k · project 14k · chat 5k · memory 2k
```

During answering:

```text
answering · in ~82k · out ~640 · window 128k · raw 74%
```

For exact token usage:

- Estimate locally for all backends first.
- Use API `usage` when OpenAI-compatible backends return it.
- Add streaming/output counters later where backend support exists.
- Parse observed-agent usage from Claude/Codex transcripts if available.

## Prompt Posture

The Copilot should say what it knows from evidence, not talk about briefs or
summaries.

Bad:

```text
The brief does not say the keeper yield.
```

Good:

```text
I do not have the full output table in the current context. I can see the agent
claimed the overnight funnel produced keepers, but the exact keeper yield
requires expanding the raw assistant message around [L3040].
```

Once retrieval can expand that line, it should answer directly from the raw
record.

## Implementation Phases

### v0.7.0: Evidence Expansion

- Add raw transcript chunk retrieval by line, keyword, and recent tail.
- Feed expanded raw evidence into `ask`/`chat`/`cockpit`.
- Include paired tool call/result records together.
- Keep deterministic `brief`, `observe`, `check`, and `status` unchanged.
- Replace fixed recent-turn replay with a budgeted compatibility path.

### v0.7.1: Context HUD

- Add estimated token/context usage to the TUI status line. **Implemented.**
- Show input/context estimates while answering. **Implemented with local input
  estimates and post-answer output estimates.**
- Split estimates by raw transcript, project facts, cockpit chat, memory, and
  summary index. **Implemented; memory remains `0` until v0.7.2 durable memory
  lands.**

### v0.7.2: Budget-Aware Chat Memory

- Replace raw-only old cockpit replay with budgeted recent turns plus durable
  structured memory.
- Keep the complete raw Cockpit Session log on disk.
- Add explicit compaction triggers and recovery tests.

### v0.7.3: Project Context Budgeting

- Make project facts tiered: git summary, changed files, key docs, relevant
  excerpts, broader file index.
- Retrieve project snippets relevant to the question instead of always sending
  the same project packet.

### v0.8: Streaming And Exact Usage

- Add streaming backend support where possible.
- Report exact input/output token usage when backend APIs expose it.
- Add richer HUD behavior for live output counters and coverage changes.

## Non-Negotiables

- cc-copilot remains read-only.
- Deterministic `brief`, `observe`, `check`, and `status` stay.
- Summaries are allowed as indexes, not ground truth.
- Every specific claim should be traceable to primary evidence.
- When coverage is incomplete, the UI should say so honestly.

## v0.7.0 Acceptance Criteria

- A question about a detail buried inside an earlier full assistant message can
  retrieve that full message when keywords match.
- A question citing `[L123]` retrieves nearby raw records and paired tool
  evidence.
- Multi-session retrieval keeps session-qualified citations.
- Cockpit conversation history is included by budget, not by a fixed turn count.
- The model-facing prompt no longer exposes "brief" as the agent's identity.
- Existing deterministic commands keep their output shape.
