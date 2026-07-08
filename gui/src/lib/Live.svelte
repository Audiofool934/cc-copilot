<script lang="ts">
  import { onDestroy } from "svelte";
  import { marked } from "marked";
  import { surfaces } from "$lib/jsonrpc";

  let { sessionPath, scope = "session", scopeSessions = "" } = $props<{ sessionPath: string; scope?: string; scopeSessions?: string }>();

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
  let alerts = $state(false);
  // Token guard + mounted flag: a slow poll from a previous session can't
  // write stale brief/verdict/status into the current one, and no writes land
  // after unmount.
  let tickToken = 0;
  let mounted = true;

  async function notify(title: string, body: string) {
    if (!alerts || typeof Notification === "undefined") return;
    try {
      if (Notification.permission === "default") await Notification.requestPermission();
      if (Notification.permission === "granted") new Notification(title, { body });
    } catch { /* notifications unavailable - silent */ }
  }

  async function toggleAlerts() {
    alerts = !alerts;
    if (alerts && typeof Notification !== "undefined" && Notification.permission === "default") {
      try { await Notification.requestPermission(); } catch { /* ignore */ }
    }
  }

  async function tick() {
    if (!sessionPath) return;
    const token = ++tickToken;
    try {
      const [b, v, st] = await Promise.all([
        surfaces.brief({ session: sessionPath, scope, scope_sessions: scopeSessions }),
        surfaces.checkVerdict({ session: sessionPath, scope, scope_sessions: scopeSessions }),
        surfaces.state(sessionPath),
      ]);
      if (!mounted || token !== tickToken) return;
      const prevStatus = status;
      brief = b;
      events = st.events;
      status = st.status;
      if (prevVerdict !== null && v !== prevVerdict) {
        transition = v === 2 ? "verdict escalated to intervene"
          : v === 1 ? "verdict rose to review"
          : "verdict cleared";
      } else transition = "";
      const escalate = v === 2 || (st.status === "stalled" && prevStatus !== "stalled");
      if (escalate) notify("cc-copilot needs attention", transition || `status ${st.status}`);
      verdict = v;
      prevVerdict = v;
      updated = new Date().toLocaleTimeString();
      error = "";
    } catch (e) {
      if (mounted && token === tickToken) error = e instanceof Error ? e.message : String(e);
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

  // Reset cross-session state + reload when the session changes; restart the
  // poll loop on session/interval/play change.
  $effect(() => {
    void sessionPath;
    if (!sessionPath) { stop(); return; }
    brief = ""; verdict = null; events = 0; status = ""; updated = ""; transition = "";
    prevVerdict = null; error = "";
    tick();
  });
  $effect(() => { void sessionPath; void interval; void playing; if (sessionPath) start(); });
  onDestroy(() => { mounted = false; stop(); });

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
    <button class:active={alerts} onclick={toggleAlerts} title="desktop alert when the verdict escalates">🔔 alerts</button>
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
  .status.running { background: var(--status-running-bg); color: var(--status-running-text); }
  .status.stalled { background: var(--status-stalled-bg); color: var(--status-stalled-text); }
  .status.idle { background: var(--status-idle-bg); color: var(--status-idle-text); }
  .status.awaiting-agent { background: var(--status-awaiting-bg); color: var(--status-awaiting-text); }
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