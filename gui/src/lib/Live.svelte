<script lang="ts">
  import { onDestroy } from "svelte";
  import { marked } from "marked";
  import { surfaces } from "$lib/jsonrpc";

  let { sessionPath } = $props<{ sessionPath: string }>();

  let brief = $state("");
  let verdict = $state<number | null>(null);
  let events = $state(0);
  let status = $state("");
  let updated = $state("");
  let playing = $state(true);
  let interval = $state(2);
  let error = $state("");
  let timer: ReturnType<typeof setInterval> | null = null;
  let prevVerdict: number | null = null;
  let transition = $state("");

  async function tick() {
    if (!sessionPath) return;
    try {
      const [b, v, st] = await Promise.all([
        surfaces.brief({ session: sessionPath }),
        surfaces.checkVerdict({ session: sessionPath }),
        surfaces.state(sessionPath),
      ]);
      brief = b;
      events = st.events;
      status = st.status;
      if (prevVerdict !== null && v !== prevVerdict) {
        transition = v === 2 ? "verdict escalated to intervene"
          : v === 1 ? "verdict rose to review"
          : "verdict cleared";
      } else transition = "";
      verdict = v;
      prevVerdict = v;
      updated = new Date().toLocaleTimeString();
      error = "";
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  function start() {
    stop();
    if (!playing) return;
    timer = setInterval(tick, Math.max(1, interval) * 1000);
  }
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }
  function toggle() { playing = !playing; if (playing) start(); else stop(); }

  // (re)start polling when session or interval changes, or play toggles
  $effect(() => { if (sessionPath) { tick(); start(); } });
  onDestroy(stop);

  function render(md: string): string { return marked.parse(md || "", { breaks: false }) as string; }
  function vColor(v: number | null) { return v === 2 ? "var(--bad)" : v === 1 ? "var(--warn)" : "var(--good)"; }
  function vLabel(v: number | null) { return v === 2 ? "intervene" : v === 1 ? "review" : v === 0 ? "clear" : "—"; }
</script>

<div class="live">
  <div class="bar">
    <button class:active={playing} onclick={toggle}>{playing ? "⏸ pause" : "▶ watch"}</button>
    <label>every <input type="number" min="1" max="60" bind:value={interval} onchange={start} />s</label>
    <span class="status {status}">{status}</span>
    <span class="pill" style="color:{vColor(verdict)}; border-color:{vColor(verdict)}">{vLabel(verdict)}</span>
    <span class="events">{events} ev</span>
    <span class="updated">{updated}</span>
    {#if transition}<span class="transition">↗ {transition}</span>{/if}
  </div>
  {#if error}<div class="error">⚠ {error}</div>{/if}
  <div class="brief">
    <!-- eslint-disable-next-line svelte/no-at-html-tags -- markdown from the local cc-copilot server -->
    <div class="markdown">{@html render(brief)}</div>
  </div>
</div>

<style>
  .live { display: flex; flex-direction: column; height: 100%; min-height: 0; }
  .bar { display: flex; align-items: center; gap: 12px; padding: 4px 0 8px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .bar button { padding: 4px 12px; font-size: 12px; cursor: pointer; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 999px; }
  .bar button.active { color: var(--accent); border-color: var(--accent); }
  .bar label { font-size: 12px; color: var(--muted); }
  .bar input { width: 44px; padding: 3px 6px; font-size: 12px; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; }
  .status { font-size: 12px; padding: 2px 8px; border-radius: 4px; }
  .status.running { background: #1d2b46; color: #9ec5ff; }
  .status.stalled { background: #3d1f1f; color: #ff8b8b; }
  .status.idle { background: #1c232e; color: var(--muted); }
  .status.awaiting-agent { background: #3d3010; color: #e0c060; }
  .pill { font-size: 11px; font-weight: 600; text-transform: uppercase; padding: 2px 9px; border: 1px solid; border-radius: 999px; }
  .events { font-size: 12px; color: var(--muted); }
  .updated { font-size: 12px; color: var(--muted); margin-left: auto; }
  .transition { font-size: 12px; color: var(--warn); }
  .brief { flex: 1; overflow: auto; padding-top: 12px; }
  .markdown { max-width: 880px; line-height: 1.55; }
  .markdown :global(h1) { font-size: 18px; margin: 0 0 8px; }
  .markdown :global(h2) { font-size: 15px; margin: 18px 0 6px; }
  .markdown :global(p) { margin: 6px 0; }
  .markdown :global(ul) { margin: 6px 0; padding-left: 22px; }
  .markdown :global(li) { margin: 2px 0; }
  .markdown :global(code) { font-family: "SF Mono", ui-monospace, monospace; font-size: 12.5px; background: var(--panel-2); padding: 1px 5px; border-radius: 4px; }
  .markdown :global(blockquote) { margin: 8px 0; padding: 4px 12px; border-left: 3px solid var(--border); color: var(--muted); }
  .markdown :global(em) { color: var(--muted); }
  .error { color: var(--bad); padding: 8px 0; }
</style>