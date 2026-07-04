<script lang="ts">
  import { onMount } from "svelte";
  import { surfaces, type BackendInfo, type ModelInfo } from "$lib/jsonrpc";
  import { toast } from "$lib/toasts.svelte";

  let backends = $state<BackendInfo[]>([]);
  let selected = $state("");
  let models = $state<ModelInfo[]>([]);
  let model = $state("");
  let key = $state("");
  let busy = $state(false);
  let saved = $state(false);
  let error = $state("");

  async function load() {
    try {
      backends = await surfaces.backends();
      const active = backends.find((b) => b.active);
      if (active) await select(active.name);
    } catch (e) { error = e instanceof Error ? e.message : String(e); }
  }

  async function select(name: string) {
    selected = name;
    key = "";
    try {
      models = await surfaces.modelsFor(name);
      const be = backends.find((b) => b.name === name);
      model = be?.default_model || models[0]?.id || "";
    } catch (e) { error = e instanceof Error ? e.message : String(e); }
  }

  async function save() {
    if (!selected || busy) return;
    busy = true; saved = false; error = "";
    try {
      await surfaces.setBackend({ name: selected, model, key: key || undefined });
      saved = true;
      toast("backend saved", "ok");
      backends = await surfaces.backends();
      setTimeout(() => (saved = false), 1500);
    } catch (e) { error = e instanceof Error ? e.message : String(e); toast(error, "error"); }
    finally { busy = false; }
  }

  onMount(load);
</script>

<div class="settings">
  <h2>Backend &amp; model</h2>
  <p class="muted">The LLM that powers narration, chat, /now, /goal, /loop. The deterministic core (brief/check/observe) needs none.</p>
  {#if error}<div class="error">⚠ {error}</div>{/if}
  <div class="grid">
    {#each backends as b}
      <button class="card" class:active={selected === b.name} onclick={() => select(b.name)}>
        <div class="row1">
          <span class="name">{b.name}</span>
          {#if b.active}<span class="tag active">active</span>{/if}
          <span class="tag" class:ok={b.available} class:bad={!b.available}>{b.available ? "ready" : "unavailable"}</span>
        </div>
        {#if !b.available && b.reason}<div class="reason">{b.reason}</div>{/if}
        {#if b.needs_key && b.key_env}<div class="reason">needs key · {b.key_env}</div>{/if}
      </button>
    {/each}
  </div>

  {#if selected}
    <div class="form">
      <label>model
        <select bind:value={model}>
          {#each models as m}<option value={m.id}>{m.id}{m.note ? ` — ${m.note}` : ""}</option>{/each}
        </select>
      </label>
      {#if backends.find((b) => b.name === selected)?.needs_key}
        <label>{backends.find((b) => b.name === selected)?.key_env}
          <input type="password" bind:value={key} placeholder="paste API key (stored in ~/.cc-copilot.toml)" />
        </label>
      {/if}
      <button class="save" onclick={save} disabled={busy}>{busy ? "…" : "save"}</button>
      {#if saved}<span class="saved">saved</span>{/if}
    </div>
  {/if}
</div>

<style>
  .settings { max-width: 760px; }
  h2 { font-size: 17px; margin: 0 0 4px; }
  .muted { color: var(--muted); font-size: 13px; margin: 0 0 16px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
  .card { text-align: left; padding: 12px 14px; background: var(--panel); border: 1px solid var(--border); border-radius: 10px; cursor: pointer; color: var(--text); }
  .card.active { border-color: var(--accent); }
  .row1 { display: flex; align-items: center; gap: 8px; }
  .name { font-weight: 600; font-size: 14px; }
  .tag { font-size: 10px; padding: 1px 7px; border-radius: 999px; text-transform: uppercase; letter-spacing: 0.03em; }
  .tag.ok { background: #16331f; color: #4fd07a; }
  .tag.bad { background: #3d1f1f; color: #ff8b8b; }
  .tag.active { background: #1d2b46; color: #9ec5ff; }
  .reason { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .form { margin-top: 20px; display: flex; flex-direction: column; gap: 12px; max-width: 460px; }
  .form label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 4px; }
  .form select, .form input { padding: 8px 10px; font-size: 13px; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 8px; outline: none; }
  .form select:focus, .form input:focus { border-color: var(--accent); }
  .save { align-self: flex-start; padding: 9px 22px; font-size: 13px; font-weight: 500; background: var(--accent); color: #0f1115; border: none; border-radius: 8px; cursor: pointer; }
  .save:disabled { opacity: 0.5; }
  .saved { color: var(--good); font-size: 13px; }
  .error { color: var(--bad); padding: 8px 0; }
</style>