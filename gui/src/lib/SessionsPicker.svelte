<script lang="ts">
  import { surfaces, type SessionRef } from "$lib/jsonrpc";

  let { cwd, scopeSessions = $bindable(""), sessionPath = $bindable("") } = $props<{
    cwd: string; scopeSessions?: string; sessionPath?: string;
  }>();

  let sessions = $state<SessionRef[]>([]);
  let open = $state(false);

  const selected = $derived(new Set((scopeSessions || "").split(/[\s,]+/).filter(Boolean)));

  async function load() { if (!cwd) return; try { sessions = await surfaces.sessions(cwd, true); } catch { /* */ } }

  $effect(() => { if (open && cwd) load(); });

  function toggle(id: string) {
    const s = new Set(selected);
    if (s.has(id)) s.delete(id); else s.add(id);
    scopeSessions = [...s].join(",");
  }

  function rowId(s: SessionRef): string { return s.session_id || s.path; }

  function close(e: MouseEvent) {
    // close on outside click handled by a backdrop
    open = false;
  }
</script>

<div class="picker">
  <button class="trigger" class:active={open} onclick={() => (open = !open)} disabled={!cwd} title="select sessions for multi/project scope">
    sessions {selected.size > 0 ? `(${selected.size})` : ""}
  </button>
  {#if open}
    <button class="backdrop" aria-label="close" onclick={close}></button>
    <div class="panel">
      <div class="hint">Check sessions to include in multi/project scope; → sets the anchor.</div>
      {#each sessions as s}
        <div class="row">
          <input type="checkbox" checked={selected.has(rowId(s))} onchange={() => toggle(rowId(s))} />
          <span class="meta">
            <span class="t">{s.title || s.session_id.slice(0, 8)}</span>
            <span class="sub">{s.agent} · {s.hhmm}{s.live ? " · live" : ""}</span>
          </span>
          <button class="anchor" class:active={sessionPath === s.path} onclick={() => { sessionPath = s.path; open = false; }} title="use as anchor">→</button>
        </div>
      {/each}
      {#if !sessions.length}<div class="empty">no sessions for this project</div>{/if}
    </div>
  {/if}
</div>

<style>
  .picker { position: relative; }
  .picker .trigger {
    font-size: 12px; padding: 4px 10px; background: var(--panel); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px; cursor: pointer; height: 30px;
  }
  .picker .trigger.active { color: var(--accent); border-color: var(--accent); }
  .backdrop { position: fixed; inset: 0; z-index: 90; border: none; background: transparent; padding: 0; cursor: default; }
  .panel { position: absolute; top: calc(100% + 4px); left: 0; z-index: 91; min-width: 320px; max-width: 420px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);
    padding: 6px; max-height: 340px; overflow: auto; }
  .hint { font-size: 11px; color: var(--muted); padding: 4px 6px 8px; }
  .row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 6px; }
  .row:hover { background: var(--panel-2); }
  .row .meta { display: flex; flex-direction: column; flex: 1; min-width: 0; }
  .t { font-size: 13px; }
  .sub { font-size: 11px; color: var(--muted); }
  .anchor { font-size: 13px; padding: 2px 8px; background: transparent; color: var(--muted); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; }
  .anchor.active { color: var(--accent); border-color: var(--accent); }
  .empty { color: var(--muted); font-size: 12px; padding: 12px; }
</style>