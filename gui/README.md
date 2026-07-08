# cc-copilot GUI

The standalone desktop GUI for [cc-copilot](../README.md) — a Tauri +
SvelteKit + TypeScript shell over the Python core.

The Tauri Rust shell (`src-tauri/`) spawns `cc-copilot serve` (the localhost
JSON-RPC/SSE server in `../cccopilot/server.py`) and the SvelteKit frontend
(`src/`) drives it through the typed client in `src/lib/jsonrpc.ts` and the
streaming client in `src/lib/stream.ts`. The same `cccopilot/api.py` facade
backs both the CLI/TUI and this GUI, so behaviour is identical across surfaces.

## Run

The Python core must be importable on PATH (the shell runs `cc-copilot serve`):

```bash
pip install -e "..[tui]"      # from the repo root, once
```

Then:

```bash
npm install
npm run tauri dev             # desktop app with hot reload
npm run tauri build           # production build
```

`npm run check` runs `svelte-check`; `npm run build` builds the frontend alone.

## Layout

- `src/routes/+page.svelte` — app chrome: project/session/scope selectors,
  verdict pill, theme picker, tab bar, and the panel switcher.
- `src/lib/` — feature components:
  - `Chat.svelte` — streaming grounded chat with slash palette, rewind, /new,
    /forget, and resumable cockpit conversations.
  - `Timeline.svelte` — native activity timeline (polling + CSS
    content-visibility virtualization).
  - `Live.svelte` / `Watch.svelte` — live watch + narrated watch monitor.
  - `Diff.svelte` — structured `/diff` view.
  - `Drafts.svelte` — `/now` / `/goal` / `/loop` / recap / `/handoff` drafts.
  - `SessionsPicker.svelte`, `ScopeGroups.svelte`, `ResumeBrowser.svelte` —
    evidence scope + cockpit session pickers.
  - `Settings.svelte`, `Welcome.svelte` — backend/model picker + onboarding.
  - `themes.ts` — curated theme palettes mirrored from the TUI.
- `src-tauri/src/lib.rs` — Rust shell: owns the `cc-copilot serve` process
  and exposes `server_port` to the frontend.

## Recommended IDE Setup

[VS Code](https://code.visualstudio.com/) + [Svelte](https://marketplace.visualstudio.com/items?itemName=svelte.svelte-vscode) + [Tauri](https://marketplace.visualstudio.com/items?itemName=tauri-apps.tauri-vscode) + [rust-analyzer](https://marketplace.visualstudio.com/items?itemName=rust-lang.rust-analyzer).