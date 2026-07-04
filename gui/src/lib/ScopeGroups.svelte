<script lang="ts">
  import { surfaces, type ScopeGroup } from "$lib/jsonrpc";
  import { toast } from "$lib/toasts.svelte";

  let { scope = $bindable("session"), scopeSessions = $bindable("") } = $props<{
    scope?: "session" | "multi-session" | "project";
    scopeSessions?: string;
  }>();

  let groups = $state<ScopeGroup[]>([]);
  let open = $state(false);
  let name = $state("");
  let busy = $state(false);

  async function load() {
    try { groups = await surfaces.scopeGroups(); }
    catch (e) { toast(String(e), "error"); }
  }

  $effect(() => { if (open) load(); });

  function sessionsLabel(g: ScopeGroup): string {
    if (g.scope === "session") return "";
    const n = (g.scope_sessions || []).length;
    return n ? `:${n}` : ":all";
  }

  async function saveCurrent() {
    const key = name.trim();
    if (!key || busy) return;
    busy = true;
    try {
      await surfaces.scopeGroupSave({ name: key, scope, scope_sessions: scopeSessions });
      toast(`saved scope group ${key}`, "ok");
      name = "";
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally { busy = false; }
  }

  function onNameKey(e: KeyboardEvent) {
    if (e.key === "Enter") saveCurrent();
  }

  async function loadGroup(g: ScopeGroup) {
    try {
      const loaded = await surfaces.scopeGroupLoad(g.name);
      if (!loaded) { toast(`scope group ${g.name} not found`, "error"); return; }
      scope = loaded.scope as "session" | "multi-session" | "project";
      scopeSessions = (loaded.scope_sessions || []).join(",");
      open = false;
      toast(`loaded ${g.name}`, "ok");
    } catch (e) { toast(String(e), "error"); }
  }

  async function deleteGroup(g: ScopeGroup) {
    try {
      const ok = await surfaces.scopeGroupDelete(g.name);
      if (ok) toast(`deleted ${g.name}`, "ok");
      await load();
    } catch (err) { toast(String(err), "error"); }
  }

  function close() { open = false; }
</script>

<div class="picker">
  <button class:active={open} onclick={() => (open = !open)} title="saved scope groups">
    groups {groups.length > 0 ? `(${groups.length})` : ""}
  </button>
  {#if open}
    <button class="backdrop" aria-label="close" onclick={close}></button>
    <div class="panel">
      <div class="save-row">
        <input type="text" bind:value={name} onkeydown={onNameKey} placeholder="name" maxlength="40" />
        <button onclick={saveCurrent} disabled={busy || !name.trim()}>{busy ? "…" : "save"}</button>
      </div>
      <div class="hint">Save the current scope, or load a saved one.</div>
      <div class="list">
        {#each groups as g}
          <div class="row">
            <button class="load" onclick={() => loadGroup(g)}>
              <span class="meta">
                <span class="name">{g.name}</span>
                <span class="sub">{g.scope}{sessionsLabel(g)}</span>
              </span>
            </button>
            <button class="delete" onclick={() => deleteGroup(g)} title="delete" aria-label="delete {g.name}">×</button>
          </div>
        {:else}
          <div class="empty">no saved scope groups</div>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .picker { position: relative; }
  .picker > button {
    font-size: 12px; padding: 4px 10px; background: var(--panel); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px; cursor: pointer; height: 30px;
  }
  .picker > button.active { color: var(--accent); border-color: var(--accent); }
  .backdrop { position: fixed; inset: 0; z-index: 90; border: none; background: transparent; padding: 0; cursor: default; }
  .panel { position: absolute; top: calc(100% + 4px); left: 0; z-index: 91; min-width: 260px; max-width: 320px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);
    padding: 10px; }
  .save-row { display: flex; gap: 8px; margin-bottom: 8px; }
  .save-row input { flex: 1; padding: 5px 8px; font-size: 12px; background: var(--panel-2); color: var(--text); border: 1px solid var(--border); border-radius: 6px; }
  .save-row button { padding: 5px 12px; font-size: 12px; background: var(--accent); color: #0f1115; border: none; border-radius: 6px; cursor: pointer; }
  .save-row button:disabled { opacity: 0.5; }
  .hint { font-size: 11px; color: var(--muted); margin-bottom: 8px; }
  .list { display: flex; flex-direction: column; gap: 2px; max-height: 260px; overflow: auto; }
  .row { display: flex; align-items: center; gap: 4px; padding: 2px; border-radius: 6px; }
  .row:hover { background: var(--panel-2); }
  .load { display: flex; align-items: center; flex: 1; gap: 8px; padding: 5px 6px; border-radius: 6px; background: transparent; border: none; color: var(--text); cursor: pointer; text-align: left; }
  .meta { display: flex; flex-direction: column; flex: 1; min-width: 0; }
  .name { font-size: 13px; }
  .sub { font-size: 11px; color: var(--muted); }
  .delete { font-size: 16px; line-height: 1; color: var(--muted); padding: 2px 6px; border-radius: 6px; background: transparent; border: none; cursor: pointer; }
  .delete:hover { background: var(--bad); color: #fff; }
  .empty { color: var(--muted); font-size: 12px; padding: 12px 4px; }
</style>
