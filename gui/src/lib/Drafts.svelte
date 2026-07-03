<script lang="ts">
  import { marked } from "marked";
  import { streamMethod } from "$lib/stream";
  import { surfaces } from "$lib/jsonrpc";

  let { sessionPath } = $props<{ sessionPath: string }>();

  let instruction = $state("");
  let when = $state("30m");
  let result = $state("");
  let busy = $state(false);
  let error = $state("");
  let copied = $state(false);

  type Kind = "now" | "goal" | "loop" | "recap" | "handoff";
  let kind = $state<Kind | "">("");

  async function run(k: Kind) {
    if (!sessionPath || busy) return;
    kind = k;
    busy = true;
    error = "";
    result = "";
    copied = false;
    try {
      if (k === "recap") {
        result = await surfaces.recapSince({ session: sessionPath, when, instruction });
      } else if (k === "handoff") {
        result = await surfaces.handoff({ session: sessionPath });
      } else {
        const method = `${k}_stream`;
        await streamMethod(method, { session: sessionPath, instruction },
          (c) => { result += c; });
      }
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function copy() {
    try { await navigator.clipboard.writeText(result); copied = true;
      setTimeout(() => (copied = false), 1500); } catch { /* ignore */ }
  }

  function render(md: string): string { return marked.parse(md || "", { breaks: false }) as string; }

  const LABELS: Record<Kind, string> = { now: "/now", goal: "/goal", loop: "/loop", recap: "recap since", handoff: "handoff" };
</script>

<div class="drafts">
  <div class="controls">
    <input bind:value={instruction} placeholder="optional steering (e.g. 'check CI every 5m')" />
    {#if kind === "recap"}
      <input class="when" bind:value={when} placeholder="when (30m / 2h / last-look)" />
    {/if}
    {#each ["now", "goal", "loop", "recap", "handoff"] as k}
      <button class:active={kind === k} onclick={() => run(k as Kind)} disabled={busy || !sessionPath}>
        {LABELS[k as Kind]}
      </button>
    {/each}
  </div>
  {#if error}<div class="error">⚠ {error}</div>{/if}
  <div class="result">
    {#if result}
      <button class="copy" onclick={copy} disabled={busy}>{copied ? "copied" : "copy"}</button>
      <div class="md">{@html render(result)}{#if busy}<span class="cursor">▋</span>{/if}</div>
    {:else if !busy}
      <div class="empty">Pick a draft above. /goal and /loop produce paste-ready agent commands;
        /now recommends the next step; recap narrates what changed since a window.</div>
    {/if}
  </div>
</div>

<style>
  .drafts { display: flex; flex-direction: column; height: 100%; gap: 12px; }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .controls input { flex: 1; min-width: 200px; padding: 7px 10px; font-size: 13px;
    background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 8px; outline: none; }
  .controls input.when { flex: 0 0 140px; }
  .controls input:focus { border-color: var(--accent); }
  .controls button { padding: 7px 14px; font-size: 13px; font-weight: 500; cursor: pointer;
    background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 8px; }
  .controls button.active { background: var(--accent); color: #0f1115; border-color: var(--accent); }
  .controls button:disabled { opacity: 0.5; cursor: default; }
  .result { flex: 1; overflow: auto; position: relative; background: var(--panel);
    border: 1px solid var(--border); border-radius: 10px; padding: 16px 20px; }
  .copy { position: absolute; top: 10px; right: 12px; font-size: 11px; padding: 3px 10px;
    border: 1px solid var(--border); border-radius: 6px; background: var(--panel-2);
    color: var(--muted); cursor: pointer; }
  .copy:disabled { opacity: 0.6; }
  .md { max-width: 880px; line-height: 1.55; }
  .md :global(h1) { font-size: 16px; } .md :global(h2) { font-size: 14px; margin: 14px 0 4px; }
  .md :global(p) { margin: 6px 0; } .md :global(ul) { margin: 6px 0; padding-left: 22px; }
  .md :global(li) { margin: 2px 0; }
  .md :global(code) { font-family: "SF Mono", ui-monospace, monospace; font-size: 12.5px;
    background: var(--panel-2); padding: 1px 5px; border-radius: 4px; }
  .md :global(pre) { background: var(--panel-2); padding: 10px 12px; border-radius: 8px; overflow: auto; }
  .md :global(pre code) { background: none; padding: 0; }
  .md :global(blockquote) { margin: 8px 0; padding: 4px 12px; border-left: 3px solid var(--border); color: var(--muted); }
  .cursor { display: inline-block; width: 0.5em; animation: blink 1s steps(1) infinite; color: var(--accent); }
  @keyframes blink { 50% { opacity: 0; } }
  .error { color: var(--bad); }
  .empty { color: var(--muted); }
</style>