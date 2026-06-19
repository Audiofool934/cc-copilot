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

- `/watch` starts watching the currently attached live session.
- `/watch stop` exits watch mode.
- `/watch status` reports scope, elapsed time, last update, next digest, and
  whether model narration is active.
- Future auto-watch behavior must be a separate opt-in setting or command, never
  implied by opening the TUI.

The watched scope must remain visible:

- HUD: `watch:on`, elapsed time, watched title/session, last update age.
- If the attached session or evidence store changes, watch should pause or reset
  visibly instead of silently narrating a new target.
- Multi-session watch should remain explicit; the first implementation should
  prefer one attached session.

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

- HUD shows watch state above the prompt box near attached sessions.
- Chat receives marked watch updates, but watch updates should be visually
  distinguishable from assistant answers.
- A future dedicated watch panel can replace the current activity-first view while
  watch mode is active:

```text
WATCH · <session title> · 18m · last 32s · next digest 2m
Phase: testing
Latest: pytest is still running against parser changes.
Digest: Since the last check, the agent finished edits, started verification,
and is waiting on the test run. No human action yet.
Needs attention: none
```

The first implementation can stay in chat + HUD, but the state model should not
assume chat is the only long-term surface.

## Opt-In And Safety

Watch must be boringly explicit:

- Starting watch is an explicit user command.
- Stopping watch is always available and cheap.
- Model narration is shown as `watch · copilot`.
- Fallback summaries are shown as deterministic watch updates.
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

Add digest accumulation and a digest renderer. The first digest can be manual
with `/watch digest`; then enable timed digest once tests prove it does not spam.

### Slice 4: Watch TUI Surface

Add a compact watch status block or mode-specific HUD line. Do not remove the
activity panel; instead, make it clear which text is raw activity and which text
is copilot interpretation.

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

