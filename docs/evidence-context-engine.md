# Evidence Context Engine

Status: proposed for v0.7  
Date: 2026-06-08

## Problem

The cockpit agent currently answers from a rendered evidence brief. That is
faithful and easy to verify, but it can feel cramped:

- The agent may over-reference "the brief" instead of behaving like an operator.
- Questions that need earlier session detail may be under-answered.
- The LLM has limited room for synthesis because the prompt says the brief is
  the only ground truth.
- The user experience can feel like talking to a summary, not to a copilot that
  can inspect the session.

The deeper issue is not that cc-copilot lacks access to Claude Code session
history. It already reads the observable JSONL transcript. The issue is that
chat currently compresses that observable history into one rendered brief before
the LLM sees it.

## Principle

cc-copilot should ground answers in the complete observable context, not merely
in a small pre-rendered brief.

The cockpit agent should feel like:

> I can inspect the whole observable session history, and I will cite exactly
> what I rely on.

Not:

> I only know this brief.

## Scope Boundary

cc-copilot can read:

- Claude Code JSONL transcripts under `~/.claude` or `$CLAUDE_CONFIG_DIR`.
- Session metadata such as cwd, branch, title/name, timestamps, tool calls, tool
  results, assistant messages, and user messages.
- Selected multi-session evidence.
- Bounded read-only project context such as git status, file index, and cited
  text excerpts.
- cc-copilot's own Cockpit Session state.

cc-copilot cannot read:

- Claude's hidden internal model context.
- Private chain-of-thought.
- Any reasoning or cache that Claude Code did not write to the transcript.
- Unobservable state inside the running agent process.

The product promise should be "complete observable context," not "complete
Claude internal context."

## Design Goal

Replace "brief-only chat grounding" with an evidence context engine that can
retrieve from full observable session history while preserving citations.

The deterministic `brief`, `check`, and `observe` commands should remain. They
are compact operator surfaces. The change is specifically for conversational Q&A
in `ask`, `chat`, and `cockpit`.

## Proposed Architecture

### 1. Evidence Store

Build an indexed representation of the observable context:

- normalized transcript records
- line numbers and timestamps
- user asks
- assistant messages
- tool calls and results
- changed files
- commands and failures
- TodoWrite plans
- project facts and file excerpts
- session metadata

Every item keeps its source citation:

- transcript line: `[L123]`
- session-qualified transcript line: `[b5c53c29:L123]`
- project file line: `[path.py:L45]`
- deterministic collector fact: `[tree]`, `[git:status]`

### 2. Retrieval Layer

For each user question, retrieve a context pack rather than sending only the
general brief.

Retrieval should combine:

- recency: recent transcript tail and current status
- relevance: lexical matches against question terms, file names, commands,
  errors, and mentioned concepts
- salience: safety signals, failures, changed files, pending tools, plan items
- user intent: whether the question asks about "why", "what changed", "errors",
  "next step", "compare sessions", or "project state"
- continuity: recent Cockpit Session turns, clearly marked as prior grounded
  conversation rather than fresh evidence

The output is a compact, cited "evidence context pack" assembled from the full
observable history.

### 3. Prompt Contract

Change the prompt posture from "answer only from the brief" to:

- Observed facts must be supported by cited evidence.
- Reasoning, synthesis, and recommendations are allowed when they are clearly
  based on cited evidence.
- Hypotheses are allowed only when labeled as inference.
- If evidence is missing, say what is missing and what would need to be checked.
- Do not claim files, commands, statuses, or actions as observed unless cited.
- Do not use tools or write files.

This keeps faithfulness while giving the cockpit agent more room to be useful.

### 4. Answer Modes

The same evidence engine can support different response modes:

- **Observe**: concise operational answer with next human decision.
- **Explain**: deeper causal narrative over retrieved evidence.
- **Compare**: multi-session comparison with session-qualified citations.
- **Plan**: suggest next instruction or review checklist, labeled as
  recommendation.
- **Explore**: brainstorm possible interpretations, clearly separated from
  observed facts.

These can be implicit from user language at first. Slash commands can come later
if needed.

## Prompt Shape

The future prompt should avoid saying "brief" as the cockpit agent's identity.
Possible shape:

```text
You are cc-copilot, a read-only cockpit agent for supervising coding agents.

You have an evidence context pack assembled from complete observable session
history and bounded read-only project facts. Use it as your source of observed
facts.

Rules:
- Cite observed facts.
- You may synthesize and recommend, but keep the evidence trail visible.
- Label inference as inference.
- If evidence is missing, say what is missing.
- Do not use tools, modify files, or claim unseen actions.
```

## Context Budget Strategy

Do not paste the entire transcript into every call. Instead:

- Always include current status, safety verdict, and recent activity.
- Always include matching evidence snippets for the user's question.
- Include broader transcript windows around important cited lines.
- Include project facts only when useful or when the cockpit is answering a
  project-context question.
- Include prior Cockpit Session turns for continuity, but not as new evidence.
- Provide enough citations for the user to verify claims.

This gives the agent access to complete observable history through retrieval
without overwhelming the model context.

## Deterministic Core Remains

The existing deterministic surfaces should remain strict:

- `brief`: compact cited recap
- `check`: safety/friction verdict
- `observe`: operator attention report
- `status`: fleet board

Those surfaces are valuable exactly because they are predictable, LLM-free or
evidence-rendered, and scriptable.

The evidence context engine extends the conversational layer; it does not
replace the deterministic core.

## Implementation Phases

1. Rename prompt language.
   - Stop telling the LLM that a "brief" is its identity.
   - Refer to an "evidence context pack" instead.
   - Allow synthesis/recommendations with citation discipline.

2. Build transcript evidence indexing.
   - Preserve current `State` and `Brief` behavior.
   - Add a new structure for searchable transcript evidence items.
   - Keep all source citations.

3. Add retrieval for chat.
   - Start with deterministic lexical retrieval plus recency/salience.
   - Avoid vector databases until the simpler path is proven insufficient.

4. Expand project evidence.
   - Keep read-only bounded file facts.
   - Retrieve project snippets relevant to the question rather than always
     sending the same compact project facts.

5. Improve answer contract.
   - Split observed facts, interpretation, and recommendations in prompt rules.
   - Test that facts remain cited while answers become less timid.

6. Add regression fixtures.
   - Questions requiring earlier transcript context.
   - Questions requiring multi-session comparison.
   - Questions requiring project facts.
   - Questions where evidence is missing.
   - Questions asking for brainstorming or next-step recommendations.

## Success Criteria

- The cockpit agent no longer over-refers to "the brief."
- It can answer questions about earlier session details when the transcript
  contains the evidence.
- It can synthesize and recommend without inventing facts.
- Cited claims remain verifiable.
- Deterministic commands keep their current behavior.
- No write permissions are added.

## Non-Goals

- Do not give the LLM raw tool access.
- Do not make cc-copilot operate the watched agent.
- Do not claim access to Claude's hidden internal context.
- Do not remove deterministic `brief`, `check`, `observe`, or `status`.
- Do not weaken citation requirements for observed facts.

