<script lang="ts">
  import { surfaces, type DiffView, type TranscriptRecord, type Command, type Failure, type FileChange } from "$lib/jsonrpc";

  let { sessionPath } = $props<{ sessionPath: string }>();

  let view = $state<DiffView | null>(null);
  let when = $state("30m");
  let loading = $state(false);
  let error = $state("");
  let loadToken = 0;

  async function load() {
    if (!sessionPath) return;
    const token = ++loadToken;
    loading = true; error = "";
    try {
      const d = await surfaces.diff({ session: sessionPath, when });
      if (token === loadToken) view = d;
    } catch (e) { if (token === loadToken) error = e instanceof Error ? e.message : String(e); }
    finally { if (token === loadToken) loading = false; }
  }

  // Reload when the session or the time window changes; clear the stale view
  // on a session switch so the previous session's diff doesn't linger.
  $effect(() => {
    void sessionPath;
    if (!sessionPath) { view = null; return; }
    view = null; error = "";
    load();
  });
  $effect(() => { void when; if (sessionPath) load(); });

  function cite(line: number): string { return `[L${line}]`; }
  function cmdSummary(c: Command): string { return c.cmd; }
</script>

<div class="diff">
  <div class="bar">
    <label>since
      <select bind:value={when}>
        <option value="30m">last 30m</option>
        <option value="2h">last 2h</option>
        <option value="1d">last 1d</option>
        <option value="last-look">last look</option>
      </select>
    </label>
    {#if view}<span class="count">{view.new_events} new · cutoff L{view.cutoff_line}</span>{/if}
  </div>
  {#if error}<div class="error">⚠ {error}</div>{/if}
  {#if view}
    {#if view.nothing_new && view.message}
      <div class="empty">{view.message}</div>
    {:else if view.nothing_new}
      <div class="empty">Nothing new since {view.label}.</div>
    {:else}
      {#if view.diff && (view.diff.status_from !== view.diff.status_to || view.diff.verdict_from !== view.diff.verdict_to)}
        <div class="transition">
          {view.diff.status_from} → {view.diff.status_to}
          · safety {view.diff.verdict_from} → {view.diff.verdict_to}
        </div>
      {/if}
      {#if view.pending_ask}<div class="pending">⏳ {view.pending_ask}</div>{/if}

      {#if view.new_humans.length}
        <section><h3>Your new asks</h3>
          <ul>{#each view.new_humans as r}<li><span class="cite">{cite(r.line)}</span> {r.text}</li>{/each}</ul>
        </section>
      {/if}
      {#if view.new_agent.length}
        <section><h3>Agent's new messages</h3>
          <ul>{#each view.new_agent as r}<li><span class="cite">{cite(r.line)}</span> {r.text}</li>{/each}</ul>
        </section>
      {/if}
      {#if view.new_commands.length}
        <section><h3>Commands run</h3>
          <ul>{#each view.new_commands as c}<li><span class="cite">{cite(c.line)}</span> <code>{cmdSummary(c)}</code></li>{/each}</ul>
        </section>
      {/if}
      {#if view.new_changed_files.length}
        <section><h3>Files changed</h3>
          <ul>{#each view.new_changed_files as f}<li><code>{f.path}</code> ({f.edits}e/{f.writes}w) <span class="cite">{cite(f.last_line)}</span></li>{/each}</ul>
        </section>
      {/if}
      {#if view.new_failures.length}
        <section><h3>New failures</h3>
          <ul class="fails">{#each view.new_failures as f}<li><span class="cite">{cite(f.line)}</span> <strong>{f.tool}</strong>: {f.summary}</li>{/each}</ul>
        </section>
      {/if}
    {/if}
  {:else if loading}
    <div class="empty">loading…</div>
  {/if}
</div>

<style>
  .diff { padding: 0; }
  .bar { display: flex; align-items: center; gap: 16px; padding: 4px 0 12px; border-bottom: 1px solid var(--border); }
  .bar label { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
  .bar select { padding: 4px 8px; font-size: 12px; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; }
  .count { font-size: 12px; color: var(--muted); }
  .transition { padding: 8px 12px; margin: 12px 0; background: var(--panel-2); border-left: 3px solid var(--accent); border-radius: 4px; font-size: 13px; }
  .pending { padding: 8px 12px; margin: 8px 0; background: var(--status-awaiting-bg); border-radius: 4px; font-size: 13px; color: var(--status-awaiting-text); }
  section { margin: 16px 0; }
  h3 { font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; margin: 0 0 6px; }
  ul { margin: 0; padding-left: 20px; }
  li { margin: 3px 0; font-size: 13px; line-height: 1.5; }
  code { font-family: "SF Mono", ui-monospace, monospace; font-size: 12px; background: var(--panel-2); padding: 1px 5px; border-radius: 4px; }
  .cite { color: var(--accent); font-family: "SF Mono", ui-monospace, monospace; font-size: 11px; }
  .fails li { color: var(--bad); }
  .empty { color: var(--muted); padding: 24px 0; }
  .error { color: var(--bad); padding: 8px 0; }
</style>