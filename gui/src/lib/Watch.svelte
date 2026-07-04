<script lang="ts">
  import { onDestroy } from "svelte";
  import { marked } from "marked";
  import { surfaces } from "$lib/jsonrpc";

  let { sessionPath, scope = "session", scopeSessions = "" } = $props<{ sessionPath: string; scope?: string; scopeSessions?: string }>();

  let brief = $state("");
  let verdict = $state<number | null>(null);
  let status = $state("");
  let events = $state(0);
  let updated = $state("");
  let playing = $state(true);
  let interval = $state(3);
  let monitor = $state(true);
  let preset = $state("default");
  let cards = $state<{ text: string; ts: string }[]>([]);
  let error = $state("");
  let prevEvents = 0;
  let timer: ReturnType<typeof setInterval> | null = null;

  const PRESETS = [
    { id: "default", label: "default", instruction: "" },
    { id: "debug", label: "debug", instruction: "focus on failures, errors, and commands that exited non-zero" },
    { id: "review", label: "review", instruction: "focus on what to verify before this is safe to merge" },
    { id: "ship", label: "ship", instruction: "focus on whether the work is complete and shippable" },
  ];

  async function tick() {
    if (!sessionPath) return;
    try {
      const sp = { session: sessionPath, scope, scope_sessions: scopeSessions };
      const [b, v, st] = await Promise.all([
        surfaces.brief(sp), surfaces.checkVerdict(sp), surfaces.state(sessionPath),
      ]);
      brief = b; verdict = v; status = st.status; events = st.events;
      updated = new Date().toLocaleTimeString();
      if (monitor && st.events > prevEvents && prevEvents > 0) {
        await narrateDelta();
      }
      prevEvents = st.events;
      error = "";
    } catch (e) { error = e instanceof Error ? e.message : String(e); }
  }

  async function narrateDelta() {
    try {
      let delta = await surfaces.since({ session: sessionPath, when: "last-look", peek: false });
      if (delta.startsWith("No last-look mark") || delta.startsWith("last-look tracking is off")) return;
      const instr = PRESETS.find((p) => p.id === preset)?.instruction ?? "";
      const text = await surfaces.watchProgress({ delta_text: delta, instruction: instr });
      if (text && text.trim()) {
        cards = [...cards, { text, ts: new Date().toLocaleTimeString() }];
        if (cards.length > 50) cards = cards.slice(-50);
      }
    } catch { /* narration is best-effort */ }
  }

  function start() { stop(); if (playing) timer = setInterval(tick, Math.max(1, interval) * 1000); }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }
  function toggle() { playing = !playing; if (playing) start(); else stop(); }
  onDestroy(stop);
  $effect(() => { if (sessionPath) { void interval; void monitor; void preset; tick(); start(); } });

  function render(md: string): string { return marked.parse(md || "", { breaks: false }) as string; }
  function vColor(v: number | null) { return v === 2 ? "var(--bad)" : v === 1 ? "var(--warn)" : "var(--good)"; }
  function vLabel(v: number | null) { return v === 2 ? "intervene" : v === 1 ? "review" : v === 0 ? "clear" : "—"; }
</script>

<div class="watch">
  <div class="bar">
    <button class:active={playing} onclick={toggle}>{playing ? "⏸ pause" : "▶ watch"}</button>
    <label>every <input type="number" min="1" max="60" bind:value={interval} onchange={start} />s</label>
    <span class="status {status}">{status}</span>
    <span class="pill" style="color:{vColor(verdict)}; border-color:{vColor(verdict)}">{vLabel(verdict)}</span>
    <span class="events">{events} ev</span>
    <label class="monitor">monitor
      <input type="checkbox" bind:checked={monitor} />
    </label>
    <select bind:value={preset} onchange={start}>
      {#each PRESETS as p}<option value={p.id}>{p.label}</option>{/each}
    </select>
    <span class="updated">{updated}</span>
  </div>
  {#if error}<div class="error">⚠ {error}</div>{/if}
  <div class="main">
    <div class="brief">
      <h3>brief</h3>
      <!-- eslint-disable-next-line svelte/no-at-html-tags -- markdown from the local cc-copilot server -->
      <div class="markdown">{@html render(brief)}</div>
    </div>
    <div class="log">
      <h3>monitor</h3>
      {#if cards.length === 0}
        <div class="empty">No watch updates yet. The monitor narrates each delta as the agent works (using the last-look marker).</div>
      {:else}
        <ul>{#each cards as c}<li><span class="ts">{c.ts}</span><div class="md">{@html render(c.text)}</div></li>{/each}</ul>
      {/if}
    </div>
  </div>
</div>

<style>
  .watch { display: flex; flex-direction: column; height: 100%; min-height: 0; }
  .bar { display: flex; align-items: center; gap: 12px; padding: 4px 0 8px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .bar button { padding: 4px 12px; font-size: 12px; cursor: pointer; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 999px; }
  .bar button.active { color: var(--accent); border-color: var(--accent); }
  .bar label { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 4px; }
  .bar input[type=number] { width: 44px; padding: 3px 6px; font-size: 12px; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; }
  .bar select { padding: 3px 8px; font-size: 12px; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; }
  .status { font-size: 12px; padding: 2px 8px; border-radius: 4px; }
  .status.running { background: #1d2b46; color: #9ec5ff; } .status.stalled { background: #3d1f1f; color: #ff8b8b; }
  .status.idle { background: #1c232e; color: var(--muted); } .status.awaiting-agent { background: #3d3010; color: #e0c060; }
  .pill { font-size: 11px; font-weight: 600; text-transform: uppercase; padding: 2px 9px; border: 1px solid; border-radius: 999px; }
  .events { font-size: 12px; color: var(--muted); }
  .updated { font-size: 12px; color: var(--muted); margin-left: auto; }
  .main { flex: 1; overflow: auto; display: flex; gap: 16px; padding-top: 12px; min-height: 0; }
  .brief { flex: 1 1 60%; overflow: auto; } .log { flex: 1 1 40%; overflow: auto; }
  h3 { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 8px; }
  .markdown { max-width: 680px; line-height: 1.5; }
  .markdown :global(h1) { font-size: 16px; } .markdown :global(h2) { font-size: 14px; margin: 12px 0 4px; }
  .markdown :global(p) { margin: 5px 0; } .markdown :global(ul) { margin: 5px 0; padding-left: 20px; }
  .markdown :global(li) { margin: 2px 0; } .markdown :global(code) { font-family: "SF Mono", ui-monospace, monospace; font-size: 12px; background: var(--panel-2); padding: 1px 5px; border-radius: 4px; }
  .log ul { list-style: none; margin: 0; padding: 0; }
  .log li { padding: 8px 10px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; }
  .log .ts { font-size: 11px; color: var(--muted); font-family: "SF Mono", ui-monospace, monospace; }
  .log .md { margin-top: 4px; line-height: 1.5; }
  .log .md :global(p) { margin: 4px 0; }
  .empty { color: var(--muted); font-size: 13px; }
  .error { color: var(--bad); padding: 8px 0; }
</style>