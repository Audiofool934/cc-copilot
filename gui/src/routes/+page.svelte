<script lang="ts">
  import { onMount } from "svelte";
  import { marked } from "marked";
  import { surfaces, type SessionRef, type State } from "$lib/jsonrpc";
  import Chat from "$lib/Chat.svelte";
  import Timeline from "$lib/Timeline.svelte";
  import Drafts from "$lib/Drafts.svelte";
  import Live from "$lib/Live.svelte";
  import Diff from "$lib/Diff.svelte";

  let projects = $state<[string, number, number][]>([]);
  let cwd = $state("");
  let sessions = $state<SessionRef[]>([]);
  let sessionPath = $state("");
  let tab = $state<"chat" | "live" | "timeline" | "diff" | "drafts" | "fleet" | "brief" | "observe" | "since" | "state">("chat");
  let fleetMd = $state("");
  let fleetLoaded = $state(false);
  let brief = $state("");
  let observe = $state("");
  let since = $state("");
  let stateJson = $state<State | null>(null);
  let verdict = $state<number | null>(null);
  let loading = $state(false);
  let error = $state("");

  async function loadProjects() {
    error = "";
    try {
      projects = await surfaces.projects();
      const live = await surfaces.currentSessionPath();
      if (live) {
        // pin to the live session's project if we can find its cwd via state
        try {
          const st = await surfaces.state(live);
          cwd = st.cwd;
        } catch {
          cwd = projects[0]?.[0] ?? "";
        }
      } else if (projects.length) {
        cwd = projects[0][0];
      }
      if (cwd) await loadSessions(live ?? undefined);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  async function loadSessions(pinned?: string) {
    if (!cwd) return;
    sessions = await surfaces.sessions(cwd);
    sessionPath = pinned && sessions.some((s) => s.path === pinned) ? pinned
      : (sessions[0]?.path ?? "");
    if (sessionPath) await loadAll();
  }

  async function onCwdChange() {
    sessionPath = "";
    brief = observe = since = "";
    stateJson = null;
    verdict = null;
    await loadSessions();
  }

  async function loadAll() {
    if (!sessionPath) return;
    loading = true;
    error = "";
    try {
      const [b, o, s, st, v] = await Promise.all([
        surfaces.brief({ session: sessionPath }),
        surfaces.observe({ session: sessionPath }),
        surfaces.since({ session: sessionPath, when: "30m" }),
        surfaces.state(sessionPath),
        surfaces.checkVerdict({ session: sessionPath }),
      ]);
      brief = b;
      observe = o;
      since = s;
      stateJson = st;
      verdict = v;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function render(md: string): string {
    return marked.parse(md, { breaks: false }) as string;
  }

  function verdictColor(v: number | null): string {
    if (v === 2) return "var(--bad)";
    if (v === 1) return "var(--warn)";
    return "var(--good)";
  }
  function verdictLabel(v: number | null): string {
    return v === 2 ? "intervene" : v === 1 ? "review" : v === 0 ? "clear" : "—";
  }

  async function loadFleet() {
    if (!cwd || fleetLoaded) return;
    fleetLoaded = true;
    try { fleetMd = await surfaces.observe({ cwd, scope: "multi-session" }); }
    catch (e) { fleetMd = ""; error = e instanceof Error ? e.message : String(e); }
  }

  // load the fleet board lazily when its tab is selected, and reload on cwd change
  $effect(() => {
    if (tab === "fleet") loadFleet();
  });
  $effect(() => { cwd; fleetLoaded = false; fleetMd = ""; });

  onMount(loadProjects);
</script>

<div class="app">
  <header class="topbar">
    <div class="brand">🛰 cc-copilot</div>
    <div class="selectors">
      <label>project
        <select bind:value={cwd} onchange={onCwdChange}>
          {#each projects as p}
            <option value={p[0]}>{p[0].split("/").pop() ?? p[0]} ({p[1]})</option>
          {/each}
        </select>
      </label>
      <label>session
        <select bind:value={sessionPath} onchange={loadAll}>
          {#each sessions as s}
            <option value={s.path}>{s.hhmm} · {s.agent} · {s.title || s.session_id.slice(0, 8)}{s.live ? " · live" : ""}</option>
          {/each}
        </select>
      </label>
    </div>
    <div class="verdict" style="color:{verdictColor(verdict)}; border-color:{verdictColor(verdict)}">
      {verdictLabel(verdict)}
    </div>
  </header>

  <nav class="tabs">
    {#each ["chat", "live", "timeline", "diff", "drafts", "fleet", "brief", "observe", "since", "state"] as t}
      <button class:active={tab === t} onclick={() => (tab = t as typeof tab)}>{t}</button>
    {/each}
    <button class="refresh" onclick={loadAll} disabled={loading || !sessionPath}>
      {loading ? "…" : "refresh"}
    </button>
  </nav>

  <main class="content" class:chat={tab === "chat" || tab === "live" || tab === "timeline" || tab === "diff" || tab === "drafts"}>
    {#if error}
      <div class="error">⚠ {error}</div>
    {:else if !sessionPath && tab !== "fleet"}
      <div class="empty">No sessions for this project. Pick another project, or run an agent in this directory.</div>
    {:else if tab === "chat"}
      <Chat {sessionPath} />
    {:else if tab === "live"}
      <Live {sessionPath} />
    {:else if tab === "timeline"}
      <Timeline {sessionPath} />
    {:else if tab === "diff"}
      <Diff {sessionPath} />
    {:else if tab === "drafts"}
      <Drafts {sessionPath} />
    {:else if tab === "fleet"}
      <!-- eslint-disable-next-line svelte/no-at-html-tags -- markdown from the local cc-copilot server -->
      <div class="markdown">{@html render(fleetMd)}</div>
    {:else if tab === "state"}
      <pre class="json">{stateJson ? JSON.stringify(stateJson, null, 2) : ""}</pre>
    {:else}
      <!-- eslint-disable-next-line svelte/no-at-html-tags -- the markdown comes from the local cc-copilot server, not user input -->
      <div class="markdown">{@html render(tab === "brief" ? brief : tab === "observe" ? observe : since)}</div>
    {/if}
  </main>
</div>

<style>
  :global(:root) {
    --bg: #0f1115;
    --panel: #161922;
    --panel-2: #1c2030;
    --text: #e6e9ef;
    --muted: #8b93a7;
    --accent: #6aa9ff;
    --good: #3fb950;
    --warn: #d29922;
    --bad: #f85149;
    --border: #232838;
    font-family: -apple-system, "SF Pro Text", Inter, system-ui, sans-serif;
    font-size: 14px;
    color: var(--text);
  }
  :global(*) { box-sizing: border-box; }
  :global(body) { margin: 0; background: var(--bg); }

  .app { display: flex; flex-direction: column; height: 100vh; }

  .topbar {
    display: flex; align-items: center; gap: 16px;
    padding: 10px 16px; border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  .brand { font-weight: 600; font-size: 15px; white-space: nowrap; }
  .selectors { display: flex; gap: 12px; flex: 1; }
  .selectors label { display: flex; flex-direction: column; font-size: 11px; color: var(--muted); flex: 1; }
  .selectors select {
    margin-top: 2px; padding: 5px 8px; font-size: 13px;
    background: var(--panel-2); color: var(--text); border: 1px solid var(--border); border-radius: 6px;
  }
  .verdict {
    font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
    padding: 4px 10px; border: 1px solid; border-radius: 999px;
  }

  .tabs {
    display: flex; gap: 2px; padding: 0 12px; border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  .tabs button {
    background: none; border: none; color: var(--muted); cursor: pointer;
    padding: 9px 14px; font-size: 13px; border-bottom: 2px solid transparent;
  }
  .tabs button.active { color: var(--text); border-bottom-color: var(--accent); }
  .tabs .refresh { margin-left: auto; color: var(--accent); }

  .content { overflow: auto; padding: 20px 28px; flex: 1; }
  .content.chat { padding: 12px 16px 16px; overflow: hidden; display: flex; }

  .markdown { max-width: 880px; line-height: 1.55; }
  .markdown :global(h1) { font-size: 18px; margin: 0 0 8px; }
  .markdown :global(h2) { font-size: 15px; margin: 20px 0 6px; color: var(--text); }
  .markdown :global(h3) { font-size: 14px; margin: 16px 0 4px; }
  .markdown :global(p) { margin: 6px 0; }
  .markdown :global(ul) { margin: 6px 0; padding-left: 22px; }
  .markdown :global(li) { margin: 2px 0; }
  .markdown :global(code) {
    font-family: "SF Mono", ui-monospace, monospace; font-size: 12.5px;
    background: var(--panel-2); padding: 1px 5px; border-radius: 4px;
  }
  .markdown :global(blockquote) {
    margin: 8px 0; padding: 4px 12px; border-left: 3px solid var(--border);
    color: var(--muted);
  }
  .markdown :global(hr) { border: none; border-top: 1px solid var(--border); margin: 16px 0; }
  .markdown :global(em) { color: var(--muted); }

  .json { font-family: "SF Mono", ui-monospace, monospace; font-size: 12px; white-space: pre; color: var(--text); }

  .error { color: var(--bad); padding: 16px; }
  .empty { color: var(--muted); padding: 32px; }
</style>