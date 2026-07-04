<script lang="ts">
  import { onMount } from "svelte";
  import { surfaces } from "$lib/jsonrpc";
  import { toast } from "$lib/toasts.svelte";

  let { cwd = "", onResume } = $props<{
    cwd?: string;
    onResume?: (cwd: string, sessionPath: string, convId: string) => void;
  }>();

  interface Header {
    conv_id: string;
    session_id: string;
    cwd: string;
    transcript: string;
    title: string;
    turns: number;
    ago: string;
    transcript_present: boolean;
  }

  let sessions = $state<Header[]>([]);
  let open = $state(false);
  let busy = $state(false);

  async function load() {
    if (busy) return;
    busy = true;
    try {
      const all = await surfaces.cockpitSessions(cwd || undefined);
      sessions = all.map((h) => ({
        conv_id: String(h.conv_id || ""),
        session_id: String(h.session_id || ""),
        cwd: String(h.cwd || ""),
        transcript: String(h.transcript || ""),
        title: String(h.title || "(untitled)"),
        turns: Number(h.turns || 0),
        ago: String(h.ago || ""),
        transcript_present: Boolean(h.transcript_present),
      }));
    } catch (e) { toast(String(e), "error"); }
    finally { busy = false; }
  }

  $effect(() => { if (open) load(); });

  async function resume(h: Header) {
    if (!h.transcript_present || !h.cwd) {
      toast("session transcript is no longer available", "error");
      return;
    }
    onResume?.(h.cwd, h.transcript, h.conv_id);
    open = false;
    toast(`resumed ${h.title}`, "ok");
  }

  function close() { open = false; }
</script>

<div class="picker">
  <button class:active={open} onclick={() => (open = !open)} disabled={busy} title="resume a saved cockpit conversation">
    resume {sessions.length > 0 ? `(${sessions.length})` : ""}
  </button>
  {#if open}
    <button class="backdrop" aria-label="close" onclick={close}></button>
    <div class="panel">
      <div class="hint">Saved cockpit conversations. Pick one to switch to its session and load its chat.</div>
      <div class="list">
        {#each sessions as h}
          <button class="row" onclick={() => resume(h)} disabled={!h.transcript_present}>
            <span class="meta">
              <span class="title">{h.title || h.session_id.slice(0, 8) || "(untitled)"}</span>
              <span class="sub">{h.turns} turn{h.turns === 1 ? "" : "s"} · {h.ago}{h.transcript_present ? "" : " · transcript gone"}</span>
            </span>
          </button>
        {:else}
          <div class="empty">no saved cockpit conversations</div>
        {/each}
      </div>    </div>
  {/if}
</div>

<style>
  .picker { position: relative; }
  .picker > button {
    font-size: 12px; padding: 4px 10px; background: var(--panel); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px; cursor: pointer;
  }
  .picker > button.active { color: var(--accent); border-color: var(--accent); }
  .picker > button:disabled { opacity: 0.5; }
  .backdrop { position: fixed; inset: 0; z-index: 90; border: none; background: transparent; padding: 0; cursor: default; }
  .panel { position: absolute; top: calc(100% + 4px); left: 0; z-index: 91; min-width: 280px; max-width: 380px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);
    padding: 10px; }
  .hint { font-size: 11px; color: var(--muted); margin-bottom: 8px; }
  .list { display: flex; flex-direction: column; gap: 2px; max-height: 300px; overflow: auto; }
  .row { display: flex; align-items: center; gap: 8px; padding: 7px 8px; border-radius: 6px; background: transparent; border: none; color: var(--text); cursor: pointer; text-align: left; }
  .row:hover { background: var(--panel-2); }
  .row:disabled { opacity: 0.5; cursor: default; }
  .meta { display: flex; flex-direction: column; flex: 1; min-width: 0; }
  .title { font-size: 13px; }
  .sub { font-size: 11px; color: var(--muted); }
  .empty { color: var(--muted); font-size: 12px; padding: 12px 4px; }
</style>
