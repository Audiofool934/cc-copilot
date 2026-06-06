# Changelog

## 0.1.0 (unreleased)

First public cut. A read-only "shadow-memory" sidecar for long-running coding
agents (Claude Code, Codex, …) — faithful, evidence-cited.

**Core (zero-dependency)**
- `brief` — deterministic, evidence-cited recap; every claim cites a transcript line.
- `check` — off-track / "is it safe to continue" judgment (fail-streaks, edit-thrash,
  retry-loops, stalls, failing tests), recency-weighted; scriptable exit codes.
- `status` / `fleet` — at-a-glance board of every session in a project, neediest first.
- `ask` / `chat` — grounded LLM Q&A over the cited state (never the raw transcript).
- `state --json`, `watch`, `sessions`.
- Faithful by construction: harness `isMeta`/`isCompactSummary`/`<synthetic>` noise
  filtered; failed edits excluded; cc-copilot's own narration sessions hidden.
- Adversarially audited (3 rounds, ~150 citations): 0 fabrications, 0 critical/major.
- 43 unit tests.

**Pluggable backends**
- codex (ChatGPT OAuth, default), claude, gemini, llm, and any OpenAI-compatible
  HTTP API (deepseek, openai, openrouter, ollama, …) — stdlib-only.
- `~/.cc-copilot.toml` for default backend/model/keys.

**Cockpit TUI** (optional `cc-copilot[tui]` extra; auto-bootstrapped on first run)
- Full-screen Textual cockpit: branded theme + verdict pill, split agent-timeline /
  chat panes, per-role gutters, multiline composer, command palette, collapsible
  output, modal session/model pickers, watcher toasts, Markdown answers — with
  `[L…]` citation fidelity preserved.
