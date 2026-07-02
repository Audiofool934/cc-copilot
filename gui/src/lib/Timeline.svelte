<script lang="ts">
  import { streamMethod } from "$lib/stream";
  import { surfaces, type TranscriptRecord } from "$lib/jsonrpc";

  let { sessionPath } = $props<{ sessionPath: string }>();

  let records = $state<TranscriptRecord[]>([]);
  let title = $state("");
  let loading = $state(false);
  let error = $state("");
  let follow = $state(true);
  let scrollEl = $state<HTMLElement | null>(null);

  // Cap the DOM to the most recent records; full virtualization is a later
  // perf stage. The cap is generous enough to show the whole of typical
  // sessions without jank.
  const CAP = 1500;

  async function load() {
    if (!sessionPath) return;
    loading = true;
    error = "";
    try {
      const tr = await surfaces.transcript(sessionPath);
      records = tr.records;
      title = tr.title || tr.session_id.slice(0, 8);
      if (follow) await scrollToBottom();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  // refetch when the session changes
  $effect(() => {
    if (sessionPath) load();
  });

  async function scrollToBottom() {
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
  }

  function onScroll() {
    if (!scrollEl) return;
    const atBottom = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight < 40;
    follow = atBottom;
  }

  const KIND_LABEL: Record<string, string> = {
    human: "you", agent_text: "agent", agent_thinking: "think",
    tool_call: "call", tool_result: "result", snapshot: "snap", system: "sys",
  };

  function kindClass(k: string): string {
    return (
      k === "human" ? "k-human" :
      k === "agent_text" ? "k-agent" :
      k === "tool_call" ? "k-call" :
      k === "tool_result" ? "k-result" :
      "k-other"
    );
  }

  function callSummary(r: TranscriptRecord): string {
    if (r.kind !== "tool_call") return "";
    const inp = r.tool_input || {};
    if (r.tool_name === "Bash") return String(inp.command ?? inp.description ?? "");
    return String(inp.file_path ?? inp.notebook_path ?? inp.pattern ?? inp.query ?? "");
  }

  function resultSummary(r: TranscriptRecord): string {
    if (r.kind !== "tool_result") return "";
    return (r.text || "").slice(0, 280);
  }
</script>

<div class="timeline">
  <div class="head">
    <span class="title">{title}</span>
    <span class="count">{records.length} events</span>
    <button class="follow" class:active={follow} onclick={() => { follow = true; scrollToBottom(); }}>
      {follow ? "following" : "follow live"}
    </button>
  </div>
  {#if error}<div class="error">⚠ {error}</div>{/if}
  <div class="feed" bind:this={scrollEl} onscroll={onScroll}>
    {#if records.length > CAP}
      <div class="truncated">…showing the last {CAP} of {records.length} events</div>
    {/if}
    {#each records.slice(-CAP) as r (r.line)}
      <div class="rec {kindClass(r.kind)}">
        <div class="gutter"><span class="line">L{r.line}</span><span class="hhmm">{r.hhmm}</span></div>
        <div class="body">
          <span class="kind {kindClass(r.kind)}">{KIND_LABEL[r.kind] ?? r.kind}</span>
          {#if r.kind === "tool_call"}
            <span class="tool">{r.tool_name}</span>
            <span class="code">{callSummary(r)}</span>
          {:else if r.kind === "tool_result"}
            <span class="code result" class:err={r.is_error}>{resultSummary(r)}{#if (r.text?.length ?? 0) > 280}…{/if}</span>
          {:else}
            <span class="text">{r.text}</span>
          {/if}
        </div>
      </div>
    {/each}
  </div>
</div>

<style>
  .timeline { display: flex; flex-direction: column; height: 100%; min-height: 0; }
  .head { display: flex; align-items: center; gap: 12px; padding: 4px 0 8px; border-bottom: 1px solid var(--border); }
  .title { font-weight: 600; }
  .count { color: var(--muted); font-size: 12px; }
  .follow { margin-left: auto; font-size: 12px; padding: 3px 10px; border: 1px solid var(--border);
    border-radius: 999px; background: transparent; color: var(--muted); cursor: pointer; }
  .follow.active { color: var(--accent); border-color: var(--accent); }
  .feed { flex: 1; overflow: auto; font-family: "SF Mono", ui-monospace, monospace; font-size: 12px; }
  .truncated { color: var(--muted); padding: 8px 0; font-style: italic; }
  .rec { display: flex; gap: 10px; padding: 3px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }
  .gutter { width: 84px; flex-shrink: 0; color: var(--muted); display: flex; gap: 6px; }
  .line { opacity: 0.6; }
  .hhmm { opacity: 0.9; }
  .body { flex: 1; min-width: 0; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
  .kind { flex-shrink: 0; font-size: 10px; font-weight: 600; text-transform: uppercase; padding: 1px 5px; border-radius: 4px; }
  .k-human { color: #8b93a7; } .k-human .kind, .kind.k-human { background: #2a2f3d; color: #c8d0e0; }
  .k-agent { color: #b6c7e6; } .kind.k-agent { background: #1d2b46; color: #9ec5ff; }
  .k-call { color: #d6b6e6; } .kind.k-call { background: #2a1f3d; color: #d6a6ff; }
  .k-result { color: #9aa6b8; } .kind.k-result { background: #1c232e; color: #9aa6b8; }
  .k-other { color: var(--muted); }
  .text { white-space: pre-wrap; word-break: break-word; color: var(--text); }
  .tool { color: var(--accent); font-weight: 600; }
  .code { color: #c8d0e0; white-space: pre-wrap; word-break: break-word; }
  .code.result { color: #9aa6b8; }
  .code.err { color: var(--bad); }
  .error { color: var(--bad); padding: 8px 0; }
</style>