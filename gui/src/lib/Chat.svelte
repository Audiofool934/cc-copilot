<script lang="ts">
  import { marked } from "marked";
  import { streamMethod } from "$lib/stream";
  import { surfaces } from "$lib/jsonrpc";

  let { sessionPath, scope = "session", scopeSessions = "" } = $props<{ sessionPath: string; scope?: string; scopeSessions?: string }>();

  interface Message { role: "user" | "assistant"; text: string }
  let messages = $state<Message[]>([]);
  let draft = $state("");
  let busy = $state(false);
  let error = $state("");
  let scrollEl = $state<HTMLElement | null>(null);

  // load this session's saved cockpit conversation when it changes
  $effect(() => {
    if (!sessionPath) { messages = []; return; }
    (async () => {
      try {
        const hist = await surfaces.cockpitHistory(sessionPath);
        messages = hist.map(([r, t]) => ({ role: r as Message["role"], text: t }));
        scrollToBottom();
      } catch { /* persistence off or no history - start fresh */ }
    })();
  });

  async function send() {
    const q = draft.trim();
    if (!q || busy || !sessionPath) return;
    draft = "";
    error = "";
    const history = messages.map((m) => [m.role, m.text] as [string, string]);
    const ai: Message = { role: "assistant", text: "" };
    messages = [...messages, { role: "user", text: q }, ai];
    await scrollToBottom();
    busy = true;
    try {
      await streamMethod(
        "chat_stream",
        { session: sessionPath, history, question: q, scope, scope_sessions: scopeSessions },
        (chunk) => {
          ai.text += chunk;
          messages = [...messages]; // trigger reactivity
          scrollToBottom();
        },
      );
      // persist the completed Q&A turn (best-effort)
      surfaces.cockpitRecord({ session: sessionPath, question: q, answer: ai.text }).catch(() => {});
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function forget() {
    if (!sessionPath || busy) return;
    try { await surfaces.cockpitForget(sessionPath); } catch { /* ignore */ }
    messages = [];
    error = "";
  }

  async function scrollToBottom() {
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function render(md: string): string {
    return marked.parse(md || "", { breaks: false }) as string;
  }
</script>

<div class="chat">
  <div class="messages" bind:this={scrollEl}>
    {#if messages.length === 0}
      <div class="empty">Ask a question grounded in this session's transcript and project.
        Enter sends, Shift+Enter for a newline.</div>
    {/if}
    {#each messages as m (m)}
      <div class="msg {m.role}">
        {#if m.role === "user"}
          <div class="bubble">{m.text}</div>
        {:else}
          <div class="md">{@html render(m.text)}{#if busy && m === messages[messages.length - 1]}<span class="cursor">▋</span>{/if}</div>
        {/if}
      </div>
    {/each}
  </div>
  {#if error}<div class="error">⚠ {error}</div>{/if}
  <div class="composer">
    <textarea
      bind:value={draft}
      onkeydown={onKey}
      placeholder="Ask about this session... (Enter to send, Shift+Enter for newline)"
      disabled={busy}
      rows="2"
    ></textarea>
    <button onclick={send} disabled={busy || !draft.trim() || !sessionPath}>
      {busy ? "…" : "send"}
    </button>
    {#if messages.length}
      <button class="clear" onclick={forget} disabled={busy} title="clear this session's saved cockpit conversation">clear</button>
    {/if}
  </div>
</div>

<style>
  .chat { display: flex; flex-direction: column; height: 100%; min-height: 0; }
  .messages { flex: 1; overflow: auto; padding: 4px 0; }
  .empty { color: var(--muted); padding: 24px 0; text-align: center; }
  .msg { margin: 10px 0; }
  .msg.user { text-align: right; }
  .bubble {
    display: inline-block; max-width: 78%; text-align: left;
    background: var(--panel-2); border: 1px solid var(--border);
    padding: 8px 12px; border-radius: 12px; white-space: pre-wrap; word-wrap: break-word;
  }
  .msg.assistant .md {
    max-width: 880px; line-height: 1.55; padding: 4px 0;
  }
  .md :global(p) { margin: 6px 0; }
  .md :global(ul) { margin: 6px 0; padding-left: 22px; }
  .md :global(li) { margin: 2px 0; }
  .md :global(code) {
    font-family: "SF Mono", ui-monospace, monospace; font-size: 12.5px;
    background: var(--panel-2); padding: 1px 5px; border-radius: 4px;
  }
  .md :global(blockquote) { margin: 8px 0; padding: 4px 12px; border-left: 3px solid var(--border); color: var(--muted); }
  .cursor { display: inline-block; width: 0.5em; animation: blink 1s steps(1) infinite; color: var(--accent); }
  @keyframes blink { 50% { opacity: 0; } }
  .error { color: var(--bad); padding: 6px 0; }
  .composer { display: flex; gap: 8px; align-items: flex-end; padding-top: 8px; border-top: 1px solid var(--border); }
  textarea {
    flex: 1; resize: none; font-family: inherit; font-size: 13px; line-height: 1.4;
    padding: 8px 10px; background: var(--panel); color: var(--text);
    border: 1px solid var(--border); border-radius: 8px; outline: none;
  }
  textarea:focus { border-color: var(--accent); }
  button {
    padding: 8px 16px; font-size: 13px; font-weight: 500; cursor: pointer;
    background: var(--accent); color: #0f1115; border: none; border-radius: 8px;
  }
  button:disabled { opacity: 0.5; cursor: default; }
  .clear { background: transparent; color: var(--muted); border: 1px solid var(--border); }
</style>