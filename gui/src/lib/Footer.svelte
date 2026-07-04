<script lang="ts">
  // A compact keybinding-hint footer, mirroring the TUI's Footer. Hints are
  // contextual to the active tab; the parent passes the active tab.
  let { tab } = $props<{ tab: string }>();

  const HINTS: Record<string, string> = {
    chat: "Enter send · Shift+Enter newline · / commands · Tab accept · Esc close",
    live: "▶ watch polls the brief · 🔔 alerts escalate · adjust interval",
    watch: "monitor narrates each delta · preset focuses the narration",
    timeline: "▶ live polls the transcript · follow sticks to the bottom",
    drafts: "/now · /goal · /loop stream · recap since · copy the draft",
    diff: "pick a window · citations are transcript lines",
    brief: "evidence-cited recap · verdict pill in the topbar",
    observe: "attention board · where should your focus go",
    since: "pick a window · mark seen advances the last-look marker",
    state: "raw assessed state as JSON",
    fleet: "multi-session board for the project",
    settings: "pick the LLM backend + model · API key stored in ~/.cc-copilot.toml",
  };
</script>

<footer class="footer">
  <span class="hint">{HINTS[tab] ?? ""}</span>
  <span class="verdict-key"><span class="dot good"></span> clear <span class="dot warn"></span> review <span class="dot bad"></span> intervene</span>
</footer>

<style>
  .footer { display: flex; align-items: center; gap: 16px; padding: 5px 16px; border-top: 1px solid var(--border);
    background: var(--panel); font-size: 11px; color: var(--muted); }
  .hint { flex: 1; }
  .verdict-key { display: flex; align-items: center; gap: 8px; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 3px; vertical-align: middle; }
  .dot.good { background: var(--good); } .dot.warn { background: var(--warn); } .dot.bad { background: var(--bad); }
</style>