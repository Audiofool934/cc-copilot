# cc-copilot Watch Mode Design

Date: 2026-06-19

Status: research and product design note. This document describes the intended
shape of `/watch`; it is not a release checklist.

## Purpose

`/watch` is the opt-in long-task monitoring mode for cc-copilot. It follows an
attached coding-agent session and turns transcript growth into readable process
updates, periodic digests, and actionable attention calls.

It is not the session activity panel. The activity panel is the raw event lane:
new records, changed files, tool failures, and timeline facts. Watch mode is the
human-facing interpretation lane: "what is happening, what changed, and whether I
need to care right now."

It is also not ordinary chat. Chat answers are user-prompted and can use broader
project context. Watch updates are system-initiated only after the user opts in,
and they should be cheap, scoped to the watched session delta, and clearly marked
as observation.

## Research Grounding

Progress UX research argues against silent waits and indefinite spinners for long
tasks. NN/g recommends immediate feedback after user action and stronger progress
indicators once operations exceed roughly 10 seconds; when exact percent complete
is unavailable, running progress feedback about work done is still preferable to
silence. See:

- https://www.nngroup.com/articles/progress-indicators/
- https://www.nngroup.com/articles/response-times-3-important-limits/

SRE monitoring practice separates collection, aggregation, dashboarding, and
alerts. It also treats human interruption as a scarce resource: pages/alerts
should be reserved for conditions that need action, while less severe state
belongs in dashboards or logs. See:

- https://sre.google/sre-book/monitoring-distributed-systems/
- https://sre.google/sre-book/practical-alerting/

OpenTelemetry's signal split is a useful vocabulary even though cc-copilot should
not import an observability SDK. Traces, metrics, logs, events, and contextual
baggage are different shapes of evidence. For cc-copilot, transcript records are
logs/events, state diffs behave like metrics, and the watched session/scope is the
context that must travel with each summary. See:

- https://opentelemetry.io/docs/concepts/signals/

Human-AI interaction guidance reinforces transparency and user control: make clear
what the AI can do, make clear how well it can do it, and support correction or
dismissal. For watch mode, this means visible opt-in, clear scope, explicit stop,
and honest fallback when model narration is unavailable. See:

- https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/Guidelines-for-Human-AI-Interaction-camera-ready.pdf

## Product Contract

Watch mode must preserve cc-copilot's core invariant: read-only observation. It
does not inject prompts into the watched agent, steer commands, approve tools,
retry failures, or edit files. It only observes local evidence and writes cockpit
updates.

The user must explicitly start it:

- `/watch` starts watching the currently attached live session. In
  `multi-session` or `project` evidence scope, it watches the selected live
  session transcripts in that scope and labels each update by session.
- `/watch <preset>` starts watch with a light narration steer, for example
  `/watch 中文`, `/watch english`, or a short free-text instruction.
- `/watch stop` exits watch mode.
- `/watch view` opens the read-only watch monitor in the main TUI, replacing the
  chat area while preserving session activity above it.
- In the monitor, `Left` browses the previous watch step and `Right` moves
  forward; reaching the latest step resumes follow-latest mode.
- `/watch refresh` forces a monitor refresh; normal digests are automatic.
- `/watch status` reports scope, elapsed time, last update, next digest, and
  whether model narration is active.
- Future auto-start behavior must be a separate opt-in setting or command, never
  implied by opening the TUI.

The watched scope must remain visible:

- Attached-session HUD: evidence target only, so the next prompt's context is
  readable at a glance.
- Watch dock: `off` / `on` / `paused`, phase, queued digest, and attention state.
- If the attached session or evidence store changes, watch should pause or reset
  visibly instead of silently narrating a new target.
- Multi-session/project watch should name the selected session count and keep
  per-session boundaries visible in updates and digest evidence.
- Changing the selected scope still pauses watch. Resuming on the new scope
  requires another explicit `/watch`, which rebuilds baselines for that scope.

## Three Lanes

### Activity Lane

The existing session activity panel remains factual and dense:

- New transcript event count.
- Latest observed event.
- File-change bursts.
- Tool failure lines.
- State transitions.

This lane is for inspection.

### Watch Lane

The watch lane is process-oriented and sparse:

- Micro update: one sentence for a meaningful small diff.
- Digest: periodic synthesis of recent micro updates and raw cited deltas.
- Alert: immediate, actionable callout only for failures, stalls, interventions,
  or clear human waits.

This lane is for supervision.

### Chat Lane

Chat remains user-driven:

- The user asks questions, gives instructions to cc-copilot, or requests broader
  project context.
- Watch-generated text should be visually marked so it does not look like a
  normal answer to an unasked question.

This lane is for interaction.

## Monitoring Model

Each active watch run should keep explicit state:

- `started_at`
- `last_micro_at`
- `last_digest_at`
- `last_alert_at`
- `baseline_state`
- `last_state`
- `scope_signature`
- `phase`
- `last_micro_summary`
- `digest_buffer`
- `pending_narration`
- `model_enabled`
- `cadence`

Transcript diffs should be classified before narration:

- `heartbeat`: no meaningful transcript growth but the task is still running.
- `micro`: meaningful small progress, such as a command starting, file edits, or
  a status transition.
- `phase_change`: planning to editing, editing to testing, testing to fixing,
  finalizing, or waiting.
- `alert`: failure, stall, safety verdict escalation, or explicit human wait.
- `digest_due`: elapsed time or event count reached the digest threshold.
- `done`: terminal idle/complete state after meaningful work.

## Cadence

Default cadence should avoid spam:

- Immediate start event with the watch vow.
- Micro update no more than once every 20-45 seconds unless an alert fires.
- Digest every 3-5 minutes, or after a larger event threshold such as 20-40 new
  transcript events.
- Digest automatically on meaningful phase boundaries and completion.
- Heartbeat after a long quiet period, for example 5-10 minutes, only if the
  watched task is still running.
- Alert immediately for failures, stalls, `intervene`, or human-input waits.

These values should be constants first, then user-configurable after behavior
feels right.

## Micro Updates

Micro updates answer: "What just changed?"

They should be one concise sentence, grounded only in the new delta. Good shapes:

- `watch · copilot · The agent is still running pytest, with no new failure visible yet [L42].`
- `watch · copilot · The work moved from editing into verification; pytest is now in flight [L58].`
- `watch · needs attention · The latest test run failed in parser coverage, so this needs review before waiting longer [L77].`

They should not list every changed file, every event, or every command argument.
That remains the activity lane's job.

## Periodic Digests

Digests answer: "What happened over the last stretch?"

They should summarize accumulated micro updates plus selected raw cited evidence:

- Current phase.
- Progress since last digest.
- Files or subsystems only when they matter to the phase.
- Failures/retries and whether they appear resolved.
- Current wait state and whether the human needs to act.

The digest buffer should keep compact evidence rather than the full transcript:

- Recent micro summaries.
- New alerts.
- Phase changes.
- Last command/tool state.
- Changed-file rollup.
- New failure rollup.

The prompt should ask for 3-5 sentences, not a bullet log. If no meaningful
change occurred, suppress the digest or emit a short heartbeat instead.

## TUI State

Entering watch should make the TUI feel different without taking control away:

- The attached-session HUD stays focused on evidence scope, not process state.
- A one-line watch dock below the prompt box is clickable: off starts watch,
  on opens the monitor, paused resumes on the current scope.
- Chat can receive marked watch updates while watch is active, but they are
  ephemeral process output. When watch stops, chat is pruned back to a compact
  end summary; the monitor keeps the step-level record.
- `/watch view` opens an in-place watch monitor below the session activity panel.
  It has a top menu with `Esc` return, `Left` / `Right` step navigation,
  session tabs for multi-session/project watch, `/watch refresh`, and
  `/watch stop`.

```text
watch monitor · session 2/3 <session title> · step 4/4 · latest · 18m
PHASE
testing · active · 2m
NOW
pytest is still running against parser changes.
AUTO DIGEST
Since the last check, the agent finished edits, started verification,
and is waiting on the test run. No human action yet.
ATTENTION
none
```

The monitor is a consumption surface, not a separate page. The session activity
timeline remains visible above it, and `Shift+Up` / `Shift+Down` keep resizing
that activity panel. It reads the same watch state that chat/HUD use; it does
not parse transcripts independently.

Watch steps are coarse semantic cards, not raw log rows. When a model backend is
available, cc-copilot asks for a small machine-readable boundary decision from
the current step plus the new delta: `same` updates the current card, while `new`
starts a named semantic step. If that model decision is unavailable or cannot be
parsed, deterministic fallback creates steps from explicit watch start, phase
changes, completion, and attention/pause events. Digests and micro summaries
update the current latest step instead of creating a new page every time.

For multi-session/project scope, each transcript has an independent baseline.
Changed sessions feed into the same watch loop as target-labeled deltas, so the
monitor can summarize a cross-session run without flattening away which agent
session produced the evidence. The monitor does not mix those deltas into one
view: `Tab` / `Shift+Tab` switches sessions, while `Left` / `Right` browses
steps only within the selected session. Global watch events such as start/pause
may appear across session views, but session progress remains separated by
target.

## Opt-In And Safety

Watch must be boringly explicit:

- Starting watch is an explicit user command.
- Stopping watch is always available and cheap.
- Model narration is shown as `watch · copilot`.
- Fallback summaries are shown as deterministic watch updates.
- Digests run automatically after explicit `/watch`; the user should not have to
  remember a manual digest command during normal use.
- Watch process output should not pile up in the normal chat history after
  `/watch stop`; persistent review belongs in `/watch view`.
- No watched-agent prompt injection.
- No notifications unless `watch --notify` or a future explicit setting enables
  them.
- No persistent watch logs by default; persistence can be a later opt-in.

Scope changes are the sharp edge. If the user switches `/sessions`, changes from
single to multi-session, or changes evidence store while watch is active, the
safe default is:

1. Pause or reset the watch baseline.
2. Announce the scope change.
3. Require `/watch` or `/watch reset` if the behavior would otherwise be
   ambiguous.

## Implementation Slices

### Slice 1: Formal Watch Run State

Introduce a small internal watch-state object instead of growing scattered
fields. Keep the current `/watch`, `/watch stop`, and `/watch status` grammar.
Add explicit scope signature, last micro/digest times, digest buffer, and paused
state.

### Slice 2: Micro Narration Gate

Keep the current model-backed process summary, but add cadence and coalescing:
only narrate meaningful diffs, deduplicate equivalent activity, and always fall
back deterministically when the model is unavailable or busy.

### Slice 3: Rolling Digest

Add digest accumulation and a digest renderer. Digests are loop-driven by event
thresholds, elapsed cadence, phase changes, and completion. `/watch refresh` is
a force-refresh/debug path, not the main user workflow.

### Slice 4: Watch TUI Surface

Add a compact watch dock under the prompt box and an in-place monitor pane.
Do not remove the activity panel; instead, make it clear which text is raw
activity and which text is copilot interpretation.

### Slice 5: Configurable Modes

Add explicit modes only after defaults are validated:

- `/watch quiet`: alerts and digests only.
- `/watch normal`: default micro + digest + alerts.
- `/watch verbose`: more frequent micro updates.
- `/watch every 5m`: digest cadence.
- `/watch auto`: future explicit opt-in, not part of the first release.

## Non-Goals

- No autonomous steering of the watched agent.
- No hidden auto-watch.
- No high-frequency model calls on every transcript line.
- No pretending to know percent complete when the transcript does not expose it.
- No replacing the activity panel with LLM prose.
- No notification spam.

## Release Bar

Before shipping the long-watch version:

- Unit tests cover opt-in start/stop/status.
- Unit tests cover scope-change pause/reset behavior.
- Unit tests cover cadence suppression and alert bypass.
- Unit tests cover model summary and deterministic fallback.
- TUI tests cover HUD/watch markers and rendered watch updates.
- Full suite passes.
