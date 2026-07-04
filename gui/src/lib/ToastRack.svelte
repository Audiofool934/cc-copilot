<script lang="ts">
  import { toasts } from "$lib/toasts.svelte";
</script>

<div class="rack">
  {#each toasts as t (t.id)}
    <div class="toast {t.kind}">{t.msg}</div>
  {/each}
</div>

<style>
  .rack { position: fixed; bottom: 18px; right: 18px; z-index: 200; display: flex; flex-direction: column; gap: 8px; }
  .toast { padding: 9px 14px; font-size: 13px; border-radius: 10px; background: var(--panel);
    border: 1px solid var(--border); box-shadow: 0 8px 24px rgba(0,0,0,0.45); max-width: 360px; }
  .toast.ok { border-left: 3px solid var(--good); }
  .toast.error { border-left: 3px solid var(--bad); }
  .toast.info { border-left: 3px solid var(--accent); }
</style>