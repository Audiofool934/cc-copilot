<script lang="ts">
  import { onMount } from "svelte";
  import { marked } from "marked";
  import { surfaces, type SessionRef, type State, type TargetInfo } from "$lib/jsonrpc";
  import { THEMES, themeByName, applyTheme } from "$lib/themes";
  import Chat from "$lib/Chat.svelte";
  import Timeline from "$lib/Timeline.svelte";
  import Drafts from "$lib/Drafts.svelte";
  import Live from "$lib/Live.svelte";
  import Diff from "$lib/Diff.svelte";
  import Settings from "$lib/Settings.svelte";
  import Welcome from "$lib/Welcome.svelte";
  import Watch from "$lib/Watch.svelte";
  import SessionsPicker from "$lib/SessionsPicker.svelte";
  import ScopeGroups from "$lib/ScopeGroups.svelte";
  import ResumeBrowser from "$lib/ResumeBrowser.svelte";
  import Footer from "$lib/Footer.svelte";
  import ToastRack from "$lib/ToastRack.svelte";
  import { toast } from "$lib/toasts.svelte";

  let projects = $state<[string, number, number][]>([]);
  let cwd = $state("");
  let sessions = $state<SessionRef[]>([]);
  let sessionPath = $state("");
  let tab = $state<"chat" | "live" | "watch" | "timeline" | "diff" | "drafts" | "fleet" | "brief" | "observe" | "since" | "state" | "settings">("chat");
  let needsWelcome = $state(false);
  const savedTheme = typeof localStorage !== "undefined" ? localStorage.getItem("cc-copilot-theme") : null;
  let theme = $state<string>(themeByName(savedTheme || "") ? (savedTheme as string) : "cockpit");
  $effect(() => {
    if (typeof localStorage !== "undefined") localStorage.setItem("cc-copilot-theme", theme);
    applyTheme(theme);
  });
  let fleetMd = $state("");
  let fleetLoaded = $state(false);
  let scope = $state<"session" | "multi-session" | "project">("session");
  let scopeSessions = $state("");
  let brief = $state("");
  let observe = $state("");
  let since = $state("");
  let sinceWhen = $state("30m");
  let stateJson = $state<State | null>(null);
  let verdict = $state<number | null>(null);
  let loading = $state(false);
  let error = $state("");
  let targetOpen = $state(false);
  let targetInfo = $state<TargetInfo | null>(null);

  async function loadTarget() {
    if (!sessionPath) { targetInfo = null; return; }
    try {
      targetInfo = await surfaces.target({ session: sessionPath, scope, scope_sessions: scopeSessions });
    } catch { targetInfo = null; }
  }

  $effect(() => { if (sessionPath) { void scope; void scopeSessions; loadTarget(); } });

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

  async function goHere() {
    error = "";
    try {
      const live = await surfaces.currentSessionPath();
      if (!live) { error = "no live session detected"; return; }
      const st = await surfaces.state(live);
      const targetCwd = st.cwd;
      if (!targetCwd) { error = "could not determine live session project"; return; }
      if (!projects.some((p) => p[0] === targetCwd)) {
        projects = await surfaces.projects();
      }
      cwd = targetCwd;
      await loadSessions(live);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  async function resumeSession(targetCwd: string, targetPath: string) {
    error = "";
    try {
      if (!projects.some((p) => p[0] === targetCwd)) {
        projects = await surfaces.projects();
      }
      cwd = targetCwd;
      await loadSessions(targetPath);
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
      const sp = { session: sessionPath, scope, scope_sessions: scopeSessions };
      const [b, o, st, v] = await Promise.all([
        surfaces.brief(sp),
        surfaces.observe(sp),
        surfaces.state(sessionPath),
        surfaces.checkVerdict(sp),
      ]);
      brief = b;
      observe = o;
      stateJson = st;
      verdict = v;
      if (tab === "since") loadSince();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  async function loadSince() {
    if (!sessionPath) return;
    try {
      since = await surfaces.since({ session: sessionPath, when: sinceWhen, peek: true });
    } catch (e) {
      since = "";
      error = e instanceof Error ? e.message : String(e);
    }
  }

  async function markSeen() {
    if (!sessionPath) return;
    try { await surfaces.advanceSinceMark({ session: sessionPath }); await loadSince(); }
    catch (e) { error = e instanceof Error ? e.message : String(e); }
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
    try { fleetMd = await surfaces.status({ cwd }); }
    catch (e) { fleetMd = ""; error = e instanceof Error ? e.message : String(e); }
  }

  // load the fleet board lazily when its tab is selected, and reload on cwd change
  $effect(() => {
    if (tab === "fleet") loadFleet();
  });
  $effect(() => { cwd; fleetLoaded = false; fleetMd = ""; });
  // reload reading surfaces when the scope changes; reload since when its window changes
  $effect(() => { if (sessionPath) { void scope; void scopeSessions; loadAll(); } });
  $effect(() => { if (tab === "since" && sessionPath) { void sinceWhen; loadSince(); } });

  onMount(async () => {
    applyTheme(theme);
    try { needsWelcome = await surfaces.needsOnboarding(); } catch { /* ignore */ }
    loadProjects();
  });
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
      <label>scope
        <select bind:value={scope}>
          <option value="session">session</option>
          <option value="multi-session">multi</option>
          <option value="project">project</option>
        </select>
      </label>
      {#if scope !== "session"}
        <SessionsPicker {cwd} bind:scopeSessions bind:sessionPath />
      {/if}
      <ScopeGroups bind:scope bind:scopeSessions />
      <ResumeBrowser {cwd} onResume={resumeSession} />
      <button class="here" onclick={goHere} title="/here - jump to your current live session" disabled={loading}>here</button>
    </div>
    <div class="verdict" style="color:{verdictColor(verdict)}; border-color:{verdictColor(verdict)}">
      {verdictLabel(verdict)}
    </div>
    <div class="theme-wrap">
      <select class="theme" bind:value={theme} title="theme">
        {#each THEMES as t}<option value={t.name}>{t.label}</option>{/each}
      </select>
    </div>
    <div class="target-wrap">
      <button class="target" onclick={() => (targetOpen = !targetOpen)} title="current cockpit target">target</button>
      {#if targetOpen}
        <button class="backdrop" aria-label="close" onclick={() => targetOpen = false}></button>
        <div class="target-panel">
          {#if targetInfo}
            <div class="row"><span class="key">cockpit</span><span class="val">{targetInfo.conv_id || "—"}</span></div>
            <div class="row"><span class="key">target</span><span class="val">{targetInfo.path}</span></div>
            <div class="row"><span class="key">evidence</span><span class="val">{targetInfo.scope}{targetInfo.scope_sessions.length ? `:${targetInfo.scope_sessions.length}` : ""}</span></div>
            <div class="row"><span class="key">status</span><span class="val">{targetInfo.banner}</span></div>
          {:else}
            <div class="empty">no target selected</div>
          {/if}
        </div>
      {/if}
    </div>
  </header>

  <nav class="tabs">
    {#each ["chat", "live", "watch", "timeline", "diff", "drafts", "fleet", "brief", "observe", "since", "state", "settings"] as t}
      <button class:active={tab === t} onclick={() => (tab = t as typeof tab)}>{t}</button>
    {/each}
    <button class="refresh" onclick={loadAll} disabled={loading || !sessionPath}>
      {loading ? "…" : "refresh"}
    </button>
  </nav>

  <main class="content" class:chat={tab === "chat" || tab === "live" || tab === "watch" || tab === "timeline" || tab === "diff" || tab === "drafts"}>
    {#if error}
      <div class="error">⚠ {error}</div>
    {:else if !sessionPath && tab !== "fleet"}
      <div class="empty">No sessions for this project. Pick another project, or run an agent in this directory.</div>
    {:else if tab === "chat"}
      <Chat {sessionPath} {scope} scopeSessions={scopeSessions} goto={(t) => (tab = t as typeof tab)} />
    {:else if tab === "live"}
      <Live {sessionPath} {scope} scopeSessions={scopeSessions} />
    {:else if tab === "watch"}
      <Watch {sessionPath} {scope} scopeSessions={scopeSessions} />
    {:else if tab === "timeline"}
      <Timeline {sessionPath} />
    {:else if tab === "diff"}
      <Diff {sessionPath} />
    {:else if tab === "drafts"}
      <Drafts {sessionPath} {scope} scopeSessions={scopeSessions} />
    {:else if tab === "fleet"}
      <!-- eslint-disable-next-line svelte/no-at-html-tags -- markdown from the local cc-copilot server -->
      <div class="markdown">{@html render(fleetMd)}</div>
    {:else if tab === "state"}
      <pre class="json">{stateJson ? JSON.stringify(stateJson, null, 2) : ""}</pre>
    {:else if tab === "settings"}
      <Settings />
    {:else if tab === "since"}
      <div class="since-controls">
        <label>when
          <select bind:value={sinceWhen}>
            <option value="30m">last 30m</option>
            <option value="2h">last 2h</option>
            <option value="1d">last 1d</option>
            <option value="last-look">last look</option>
          </select>
        </label>
        <button class="mark" onclick={markSeen} disabled={!sessionPath}>mark seen</button>
      </div>
      <!-- eslint-disable-next-line svelte/no-at-html-tags -- markdown from the local cc-copilot server -->
      <div class="markdown">{@html render(since)}</div>
    {:else}
      <!-- eslint-disable-next-line svelte/no-at-html-tags -- the markdown comes from the local cc-copilot server, not user input -->
      <div class="markdown">{@html render(tab === "brief" ? brief : observe)}</div>
    {/if}
  </main>
  <Footer {tab} />
</div>

<ToastRack />

{#if needsWelcome}
  <Welcome ondone={() => { needsWelcome = false; loadProjects(); }} />
{/if}

<style>
  :global(:root) {
    /* Default theme (cockpit) - overridden by applyTheme() when JS runs. */
    --bg: #1e1e1e;
    --panel: #1e1e1e;
    --panel-2: #262626;
    --text: #c0caf5;
    --muted: #6c7086;
    --accent: #807ea6;
    --good: #9ece6a;
    --warn: #e0af68;
    --bad: #f7768e;
    --border: #353535;
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
  .theme-wrap select {
    font-size: 12px; padding: 4px 8px; background: var(--panel); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px; cursor: pointer;
  }
  .target-wrap { position: relative; }
  .target { font-size: 12px; padding: 4px 10px; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; }
  .target-wrap .backdrop { position: fixed; inset: 0; z-index: 90; border: none; background: transparent; padding: 0; cursor: default; }
  .target-panel { position: absolute; top: calc(100% + 4px); right: 0; z-index: 91; min-width: 260px; max-width: 360px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);
    padding: 10px; }
  .target-panel .row { display: flex; gap: 10px; padding: 4px 0; font-size: 12px; }
  .target-panel .key { color: var(--muted); min-width: 60px; }
  .target-panel .val { color: var(--text); word-break: break-all; }
  .target-panel .empty { color: var(--muted); font-size: 12px; padding: 8px 0; }
  .here { font-size: 12px; padding: 4px 10px; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; }
  .here:disabled { opacity: 0.5; }

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
  .since-controls { display: flex; gap: 12px; align-items: center; padding: 0 0 12px; border-bottom: 1px solid var(--border); margin-bottom: 12px; }
  .since-controls label { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
  .since-controls select { padding: 4px 8px; font-size: 12px; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; }
  .mark { font-size: 12px; padding: 4px 10px; background: var(--panel); color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; }
  .mark:disabled { opacity: 0.5; }

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