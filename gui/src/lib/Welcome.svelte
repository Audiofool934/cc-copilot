<script lang="ts">
  import { onMount } from "svelte";
  import { surfaces, type OnboardChoice } from "$lib/jsonrpc";

  let { ondone } = $props<{ ondone: () => void }>();
  let choices = $state<OnboardChoice[]>([]);
  let selected = $state("");
  let key = $state("");
  let model = $state("");
  let busy = $state(false);
  let error = $state("");

  onMount(async () => {
    try { choices = await surfaces.onboardChoices(); selected = choices.find((c) => c.ready && c.kind !== "skip")?.name ?? choices[0]?.name ?? ""; }
    catch (e) { error = e instanceof Error ? e.message : String(e); }
  });

  $effect(() => {
    const c = choices.find((x) => x.name === selected);
    model = c?.default_model || "";
  });

  async function finish() {
    if (busy) return;
    busy = true; error = "";
    try {
      // "skip" choice has name "" → write_choice("") writes a Skip config
      await surfaces.setBackend({ name: selected, model, key: key || undefined });
      ondone();
    } catch (e) { error = e instanceof Error ? e.message : String(e); }
    finally { busy = false; }
  }
</script>

<div class="overlay">
  <div class="modal">
    <h1>🛰 welcome to cc-copilot</h1>
    <p class="muted">Pick the model that powers recaps, chat, /now, /goal, /loop. The deterministic
      core (brief / check / observe) needs no model. You can change this later in Settings.</p>
    {#if error}<div class="error">⚠ {error}</div>{/if}
    <div class="choices">
      {#each choices as c}
        <button class="choice" class:active={selected === c.name} onclick={() => { selected = c.name; key = ""; }}>
          <div class="row"><span class="label">{c.label || "(skip)"}</span>
            <span class="tag" class:ok={c.ready} class:bad={!c.ready}>{c.ready ? "ready" : "needs setup"}</span></div>
          <div class="blurb">{c.blurb}</div>
          {#if !c.ready && c.status}<div class="status">{c.status}</div>{/if}
        </button>
      {/each}
    </div>
    {#if choices.find((c) => c.name === selected)?.kind === "api"}
      <label class="key">{choices.find((c) => c.name === selected)?.key_env}
        <input type="password" bind:value={key} placeholder="paste API key" />
      </label>
    {/if}
    <div class="actions">
      <button class="primary" onclick={finish} disabled={busy}>{busy ? "…" : "start"}</button>
    </div>
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
  .modal { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 28px; max-width: 560px; width: 90%; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
  h1 { font-size: 20px; margin: 0 0 6px; }
  .muted { color: var(--muted); font-size: 13px; margin: 0 0 18px; line-height: 1.5; }
  .choices { display: flex; flex-direction: column; gap: 8px; max-height: 40vh; overflow: auto; }
  .choice { text-align: left; padding: 12px 14px; background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px; cursor: pointer; color: var(--text); }
  .choice.active { border-color: var(--accent); }
  .row { display: flex; align-items: center; gap: 8px; }
  .label { font-weight: 600; }
  .tag { font-size: 10px; padding: 1px 7px; border-radius: 999px; text-transform: uppercase; }
  .tag.ok { background: #16331f; color: #4fd07a; } .tag.bad { background: #3d1f1f; color: #ff8b8b; }
  .blurb { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .status { font-size: 11px; color: var(--warn); margin-top: 2px; }
  .key { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 4px; margin-top: 14px; }
  .key input { padding: 8px 10px; font-size: 13px; background: var(--panel-2); color: var(--text); border: 1px solid var(--border); border-radius: 8px; }
  .actions { margin-top: 18px; display: flex; justify-content: flex-end; }
  .primary { padding: 10px 28px; font-size: 14px; font-weight: 600; background: var(--accent); color: #0f1115; border: none; border-radius: 8px; cursor: pointer; }
  .primary:disabled { opacity: 0.5; }
  .error { color: var(--bad); padding: 8px 0; }
</style>