# Rust Migration Note

Status: superseded  
Date: 2026-06-08 (updated 2026-07-06)

> **Update (2026-07-06):** This note is superseded. The product has since added
> a standalone GUI in [`gui/`](../gui/) — Tauri (Rust shell) + SvelteKit +
> TypeScript over the same Python core (via `cccopilot/server.py` and
> `cccopilot/api.py`). The "TypeScript rewrite adds too many moving parts"
> reasoning below was written before that surface shipped; it is retained as
> historical context only. The Python core + Textual TUI remain the primary
> terminal surface; the GUI is the native desktop surface.

## Decision

Keep cc-copilot on Python + Textual for now. If the cockpit eventually needs a
lower-level native TUI stack, prefer Rust + Ratatui/crossterm over a
TypeScript/OpenTUI rewrite.

This is not an active migration plan. It is a parking lot for the architectural
decision so we can continue polishing the product without repeatedly reopening
the language debate.

## Why Not Migrate Now

The product language is still evolving:

- Cockpit Sessions are now independent and resumable.
- `/sessions` is becoming the compact evidence selector.
- Project context is always-on baseline awareness.
- The attention surface, status header, and resume model are still being tuned.

Python + Textual lets us iterate on those ideas quickly. Migrating now would
slow product discovery and risk hardening the wrong interaction model.

## Why Rust Is The Preferred Destination

cc-copilot is a terminal-first observer cockpit, not an agent execution
platform. The long-term requirements point toward Rust:

- fast startup
- reliable terminal rendering
- low idle overhead during long-running observation
- single-binary distribution
- strong cross-platform CLI behavior
- no Node, Bun, or Zig dependency chain for users

This maps well to a Rust CLI/TUI stack using Ratatui and crossterm, similar in
spirit to Codex CLI's Rust terminal implementation.

## Why Not TypeScript/OpenTUI First

TypeScript/OpenTUI is attractive for products that want:

- a broader app platform
- a plugin and SDK ecosystem
- shared UI concepts across TUI, web, and desktop
- a server/client agent architecture

That is closer to OpenCode's shape than cc-copilot's current shape. Our core
product is a lightweight, read-only interpretation layer over existing agent
sessions. A TypeScript/OpenTUI rewrite would likely add more runtime and
distribution moving parts than the product currently needs.

## Migration Triggers

Revisit this decision only if one or more of these become real bottlenecks:

- Textual rendering jank in normal cockpit use.
- Startup time becomes a common complaint.
- Packaging or Python environment setup blocks adoption.
- Terminal compatibility problems become frequent.
- Windows support becomes a serious product requirement.
- We need a single static binary for broad distribution.
- The TUI interaction model has stabilized enough that porting is mostly
  mechanical.

## Target Architecture

If migration happens, keep the same product boundaries:

- **Core model**: parse transcripts, fold state, assess safety, render evidence.
- **Observer model**: attention queue, next human decision, session activity.
- **Cockpit model**: resumable Cockpit Sessions, evidence selection, project
  context, backend/model choice.
- **TUI shell**: Rust + Ratatui/crossterm.
- **Storage**: continue read-only behavior toward agent sessions; persist only
  cc-copilot's own cockpit state.

The Python implementation should remain the reference behavior during the
transition until the Rust version matches it.

## Suggested Phases

1. Stabilize Python semantics.
   - Keep improving the current cockpit.
   - Keep state, scope, observe, and store behavior well-covered by tests.

2. Define a portable protocol.
   - Snapshot the internal data shapes for sessions, assessments, evidence, and
     Cockpit Session metadata.
   - Add fixtures that can be consumed from any language.

3. Build a Rust read-only core prototype.
   - Parse Claude Code JSONL.
   - Render `brief`, `check`, and `observe`.
   - Match Python output on fixtures before touching the TUI.

4. Build a Rust cockpit shell.
   - Status header.
   - Activity strip.
   - Chat pane.
   - Composer.
   - `/sessions` checkbox selector.
   - `/resume` picker.

5. Parallel-run both implementations.
   - Keep Python as the trusted path.
   - Use fixture and real-session diffs to close behavior gaps.

6. Ship Rust distribution only when it is boring.
   - Single binaries for macOS, Linux, and eventually Windows.
   - Python package can remain as a reference or fallback until the Rust path is
     clearly better for users.

## Non-Goals

- Do not turn cc-copilot into an agent runner.
- Do not widen write permissions.
- Do not replace cited deterministic evidence with model-only summaries.
- Do not migrate only because competitors use Rust or TypeScript.

The migration should happen only when it makes cc-copilot simpler, faster, or
easier to install for real users.

