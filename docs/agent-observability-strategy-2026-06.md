# cc-copilot Strategy Refresh — June 2026

Date: 2026-06-14
Supersedes the direction in [agent-observability-strategy.md](agent-observability-strategy.md)
(2026-06-07) where they differ; that doc remains the product-reference survey.

This refresh is the output of a deep research pass (13-agent workflow: 7 research
arms across academic + industry sources, a synthesis, a 4-lens adversarial
critique, and a reconcile) plus a code-level audit of the current tree. Where the
earlier doc cited a single Anthropic post for "agent architecture," this one is
grounded in the human-factors and agent-reliability literature that actually
explains *why* cc-copilot's read-only, evidence-cited, re-entry design is the
right shape — and it corrects two places where the code under-delivered on a
stated invariant.

---

## 1. Headline

cc-copilot wins by being the **cross-agent, line-cited comprehension layer no
acting agent can build.** Lead with the inimitable wedges (says-vs-does,
goal-drift, guardrail-contrast, cross-agent fleet tree); demote single-vendor
parity (token gauge, approval-wait detection) to plumbing; and **never cross into
acting.** The durable moat is the *union* of: read-only + cross-agent +
line-cited + re-entry-focused + zero-instrumentation. A first-party vendor cannot
copy the cross-agent dimension without spanning a competitor's fleet, cannot copy
says-vs-does / goal-drift without shipping an agent that indicts its own output,
and cannot honestly label its own cloud blind spots.

## 2. Positioning

> cc-copilot is the read-only situation-awareness instrument for the human
> supervising AI coding agents — the one tool built to help you **understand**
> your agents (Parasuraman stages 1–2: acquire + analyze) rather than **do** more
> with them (stages 3–4: decide + act). It reads the JSONL Claude Code and Codex
> already write to disk (zero instrumentation — no SDK, no proxy, no cloud),
> folds it deterministically into session state, and answers four re-entry
> questions faster than reading raw logs. Every claim pins to `[L<n>]` — "if it
> can't cite it, it doesn't claim it" — with LLM narration strictly downstream of,
> and verifiable against, that cited evidence.

## 3. Research grounding (the part the old doc was thin on)

### Agentic-workflow foundations
The canon maps onto what cc-copilot *parses*, not what it *is*: ReAct
(arXiv:2210.03629) — the Thought/Action/Observation loop is the normalized
record; Reflexion (2303.11366) and Self-Refine (2303.17651) — reflect/retry loops
that look identical in a transcript to pathological loops (the deterministic
"productive vs thrashing" distinction is the value); Plan-and-Solve (2305.04091) —
extractable plans to track plan-vs-actual; Tree/Graph of Thoughts
(2305.10601 / 2308.09687) — agents backtrack, so a linear log reader misreads
backtracking as flailing. Anthropic's "Building Effective Agents" gives the
workflow-vs-agent line: **cc-copilot is a workflow, not an agent.** LLM-as-judge
(2306.05685) has position/verbosity/self-enhancement bias — which is exactly why
cc-copilot's verdicts are deterministic, not model-judged.

### The HCI backbone (the strongest validation)
cc-copilot is textbook automation **stages 1–2 (acquire + analyze), explicitly
not 3–4 (decide + act)** — Parasuraman-Sheridan-Wickens (2000). That is a
principled grounding for the read-only contract, not a preference. Supporting
canon: Endsley (1995) situation awareness; Lee & See (2004) trust calibration /
*appropriate reliance*; Altmann-Trafton (2002) memory-for-goals (a `/since` recap
is a literal retrieval cue reactivating a decayed task goal); Parasuraman &
Manzey (2010) automation complacency; Breznitz / cry-wolf alarm fatigue
(notify.py's leading-edge design is the correct countermeasure). Two keystone
2026 empirical papers: **arXiv:2606.05391** (devs oversee coding agents mostly
post-hoc, do almost no real-time monitoring, distrust reasoning traces — the
"says vs does" gap) and **arXiv:2602.16666** (agent reliability has plateaued and
agents are poorly calibrated — the human-as-backstop role is durable, not
transitional).

### Long-horizon / memory / failure
Lost-in-the-Middle (2307.03172) + Context Rot — evidence buried mid-prompt is
missed (motivates position-aware packing). MAST (2503.13657) — a 14-mode failure
taxonomy with fatal-vs-benign severity (basis for weighting `/observe`).
Who&When (2505.00212) — even frontier models hit ~14% step-level attribution
accuracy, so any "what went wrong" feature must surface cited candidate evidence,
never a confident root cause. "LLMs Cannot Self-Correct Reasoning Yet"
(2310.01798) — the agent's own self-assessment is not ground truth.

### Industry landscape (mid-2026)
- **Claude Code:** ~27–29 hook events + built-in OTEL; subagent transcripts at
  `~/.claude/projects/<session>/subagents/agent-*.jsonl` survive parent
  compaction; `tool_use_id`/`prompt.id` is the cross-channel join key; first-party
  `claude agents`, `/rc`, `/rewind`, Routines exist but are Claude-only,
  in-process, action-capable, not evidence-cited. OTEL content is redacted by
  default, so the JSONL stays the citeable substrate.
- **Codex:** the `event_msg` stream in `rollout-*.jsonl` carries control events
  the adapter currently discards — `ExecApprovalRequest`/`ApplyPatchApprovalRequest`
  (+ `ReviewDecision`), `TurnAborted`, `StreamError`, `token_count`/`TokenUsageInfo`
  (exact context-window usage), and per-turn `turn_context` (sandbox/approval/model).
- **"OpenClaw":** disambiguated — Peter Steinberger's self-hosted personal AI
  agent (formerly Clawdbot → Moltbot; renamed after an Anthropic trademark
  request), a Node.js gateway routing WhatsApp/Telegram/iMessage/Discord to a
  model and executing local actions; ~347K stars by Apr 2026; persists state as
  local Markdown memory docs (not JSONL). **Not** a terminal coding agent and not
  the Captain Claw game engine — a possible future adapter, not a near-term one.
- **OSS supervision category** (cmux, Claude Squad, agent-deck, claude-replay,
  …) universally *acts*; `awesome-cli-coding-agents` itself notes "no purely
  read-only supervision tools." **LLMOps** (LangSmith, Langfuse, Braintrust,
  Phoenix, AgentOps, Helicone, Datadog) serves the *builder* (dev-time tracing +
  eval) and the *SRE* (prod monitoring), all require an SDK/proxy and assume you
  own the agent. Live human supervision of a session you didn't instrument is, in
  the field's own words, **"essentially unserved."** That is cc-copilot's
  uncontested category.

## 4. Differentiators to protect

1. Read-only as a **code-enforced** invariant (now genuinely fail-closed — §6).
2. Deterministic-first verdicts, LLM strictly downstream — bias-resistant.
3. Cite-or-it-doesn't-claim — the answer to "says vs does."
4. **Cross-agent** normalized fleet (Claude + Codex in one board).
5. Re-entry (`/since`, `/brief`) as the killer surface.
6. Zero-instrumentation transcript-native adapters; privacy-first local `/handoff`.

## 5. Roadmap (reconciled, confidence-rated)

Effort S/M/L/XL · Confidence high/med/low.

### NOW
- **Content-level secret redaction before the LLM pack** — M, high. *(SHIPPED — §6)*
- **Fail-closed read-only safety gate + invariant-B regression test** — S, high. *(SHIPPED — §6)*
- **Claim-vs-evidence divergence ("says vs does")** — L, high. The flagship; the
  most inimitable + most-validated bet. *(v1 SHIPPED — §6; deepen per §7.)*
- **Position-aware evidence packing** in `context.py` — S, high. Raw cited records
  to head+tail (out of the lost-in-the-middle zone), restate the question top+bottom.
- **Defensive-parse hardening** (per-line cap + Compacted-body placeholder) — S, high.
  A 700MB Codex rollout (#24948) / multi-MB line is a real cockpit DoS today.
- **Codex control-event parse (1a)** — S, high. Emit approval/abort/error as a NEW
  `control` record kind. **Do not touch `st.status`** (derived @property, ~12 consumers).
- **Chat-dominance guard + ASCII-safe CLI invariants** — S, med.

### NEXT
- **Originating-intent field + intent-drift signal** — M, med. `st.intents` is
  last-3 only; needs a new first-intent field first. Goal-drift is white space;
  keep info/warn, gate on measured precision.
- **Cross-agent neediest-first board (1b)** — M, med. Only after 1a; adds an
  `awaiting-human` status (the sequenced cross-cutting change). Value = cross-fleet
  ranking + wait duration/history, not "is it waiting" (vendor-native).
- **Cross-agent subagent/team tree** + exact Codex context from `token_count` — L, med.
  Token read's kernel is a cited stall-cause ("silent because rate_limit_reached"),
  not a gauge.
- **Per-turn sandbox/approval escalation timeline (Codex)** — M, high. A mid-session
  jump to `danger-full-access` is missed today (`state.py` reads first turn only).

### LATER
- **Declared-vs-observed guardrail contrast** (AGENTS.md/CLAUDE.md) — gated on
  redaction + a policy-line-only extractor.
- **Calibrated-language bundle:** horizon-aware `/check` softening (Ord
  constant-hazard, directional only), appropriate-reliance-tiered verdicts,
  MAST fatal-vs-non-fatal-weighted `/observe`, suspended-decision-first `/since`.

## 6. Shipped in this pass (2026-06-14)

1. **Invariant A — content-level redaction (`cccopilot/redact.py`).** Before this,
   `scope.py` blocked secret-*named* files only (basename/suffix); inline keys in
   tracked source, `tool_result` echoes of `cat .env`, and tokens inside
   `AGENTS.md`/`CLAUDE.md` (which `context.py` deliberately ingests) flowed verbatim
   to the model. A stdlib-regex pass now scrubs token-prefix shapes, PEM key blocks,
   auth headers, and secret-named `KEY=VALUE` assignments — wired into the single
   narration chokepoint `narrate._prompt`, so every model path is covered. It
   redacts the **model-bound copy only**: the on-disk transcript, the `[L<n>]` line
   map, and the cockpit's local display are untouched (the human still sees real
   values in their own terminal). Recall over precision; idempotent; git SHAs and
   `[L<n>]` citations survive.
2. **Invariant B — fail-closed read-only gate (`backends.py`).** Safety flags were
   applied only when the installed CLI advertised them (`_flag_supported`), so a
   future CLI that renamed/dropped `--tools` would silently launch the narrator
   unguarded. Now the load-bearing flag (`--tools ""` for claude, `--sandbox
   read-only` for codex) is applied unconditionally and **fail-closed**: a CLI
   whose help positively lacks it is refused with an actionable message (use an
   HTTP backend). Empty/unprobeable help still applies the flag best-effort. A
   standing regression test asserts narrator backends are never constructed
   without `safety_args` and that a CLI lacking the read-only flag raises. The
   README claim was softened from "structurally cannot become tool-using agents"
   to "gated read-only on every supported CLI; fail-closed when the gate is
   unavailable."
3. **Says-vs-does v1 (`assess.py`).** A deterministic `claim_unverified` Signal
   (warn-only, REVIEW evidence, never INTERVENE), fired only at a closing message
   and bounded to the current turn, on two high-precision patterns: (A) claims
   tests/build pass with no passing test result this turn; (B) claims a fix after
   editing code with nothing run to verify. Emits a cited pair (the claim line +
   the missing evidence) — evidence to check, never an accusation. Cross-agent by
   construction (reads the normalized `State`).

+18 tests (`test_redact.py`, `test_backends.py`, `test_assess.py`); suite 546 green.

### Follow-on (branch `read-only-hardening`, same day)

Built incrementally, each Codex-reviewed and committed:

4. **Position-aware evidence packing** (`context.py`) — raw cited records lead the
   pack (out of the lost-in-the-middle zone), never truncated for project/index.
5. **Defensive-parse hardening** (`transcript.py`/`sources/codex.py`) — a shared
   `read_capped_lines` bounds a pathological multi-GB single line in both parse
   and Codex discovery; clipped lines count as parse errors, citations stay aligned.
6. **Codex control events** (`sources/codex.py`/`state.py`/`assess.py`) — `turn_aborted`
   / `error` captured from the `event_msg` stream as `system` records; a trailing
   control event is terminal (status `idle`, never running/stalled); warn signal
   fires only when it is the current tail turn.
7. **Exact Codex context pressure & rate limits** (`token_count`) — context occupancy
   from `last_token_usage` vs `model_context_window`; warn at rate-limit ≥95% /
   context ≥90%, cited to the token_count line.
8. **Mid-session autonomy escalation** (`turn_context` timeline) — flags sandbox →
   `danger-full-access`, approval → `never` (from any supervised mode), or network
   newly reachable; routine read-only→workspace-write stays quiet.

Suite 586 green. The Codex-adapter "now/next" items above are now largely shipped.

### Real-data schema findings (verified against 472 local Codex rollouts)

These corrected the research-derived plan — record them so the next person doesn't
re-trust the wrong field:

- **No approval-request events are persisted.** `ExecApprovalRequest` /
  `ApplyPatchApprovalRequest` (the basis for the original "blocked on approval" 1a)
  do not appear in any rollout `event_msg` stream (this setup auto-approves). The
  observable control events are `turn_aborted`, `error`, and rich `token_count`.
  "Blocked on approval" is intentionally NOT claimed.
- **`token_count.info.total_token_usage` is CUMULATIVE** across the session and
  exceeds `model_context_window` (seen at 244%). Current context occupancy is
  `last_token_usage` — using `total_token_usage` as a fullness ratio is wrong.
- **`turn_context.sandbox_policy` is an object** (`{"type": read-only |
  workspace-write | danger-full-access, network_access, …}`); `approval_policy` is
  a string (`never` | `on-request` | `untrusted` | …). read-only turns omit
  `network_access` (derive no-network from the type).

## 7. Do-NOT-do (scope-creep traps)

- **Don't cross into acting.** No freeze/rollback/retry/resume/steer/approve. The
  "smallest next decision" stays advice the human runs in their own terminal.
- **Don't build the XL hooks/OTEL HTTP-listener as a latency play** — it chases
  push-speed the vendor permanently wins, on a channel whose content is redacted
  by default (can't satisfy the citation contract), and imports the largest
  invariant-A/B surface. If ever: a receive-only spool the human's own hook writes.
- **Don't add any non-stdlib core dependency** (no OTel SDK, embeddings, entropy
  lib). Borrow the vocabulary, never the dependency.
- **Don't claim coverage of cloud/web sessions** with no local JSONL — label them
  "cloud-only, teleport to inspect." Honest scoping is a trust signal.
- **Don't let says-vs-does / intent-drift phrase output as a decision or confident
  root cause** — Who&When (~14%) makes attribution unsupportable.
- **Don't market packing / token gauge / approval-wait as differentiators** — they
  are hygiene the vendor matches per-fleet.
- **Don't touch `st.status` in the first Codex control-event bet** — it is a
  derived @property consumed across ~12 modules; `awaiting-human` is a separate,
  sequenced change.

## 8. Citation hygiene (for anyone quoting this work)

- The ~23-min resumption figure is **Mark, Gudith & Klocke 2005 "No Task Left
  Behind?"**, not Mark 2008.
- arXiv:2602.16666 finds agents are **poorly calibrated** (this strengthens the
  pitch), not "calibration improved."
- Buçinca et al. is **CHI 2021**. Do not quote precise high-reliability
  extrapolations (e.g. T99≈1/70) to users — directional claims only.

## 9. Open questions

- Redaction display policy: redact in the LLM pack, show the real value in the
  local TUI? (Shipped behavior: model-bound copy only.) Confirm this is the
  desired UX before the guardrail-contrast bet builds on it.
- Measured precision of says-vs-does / intent-drift on real Claude AND Codex
  transcripts — neither should influence a verdict tier until a precision gate is met.
- Is `claude agents --json` a pure read (no session/telemetry side effects) before
  wiring it as a secondary ingest?
- Fast/slow test split before the L bets land (subagent discovery, golden fixtures)?
