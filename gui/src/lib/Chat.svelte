<script lang="ts">
  import { marked } from "marked";
  import { streamMethod } from "$lib/stream";
  import { surfaces } from "$lib/jsonrpc";
  import { toast } from "$lib/toasts.svelte";

  let { sessionPath, scope = "session", scopeSessions = "", goto = () => {}, activeConvId = $bindable<string | null>(null) } = $props<{ sessionPath: string; scope?: string; scopeSessions?: string; goto?: (t: string) => void; activeConvId?: string | null }>();

  interface Message { role: "user" | "assistant"; text: string }
  let messages = $state<Message[]>([]);
  let draft = $state("");
  let busy = $state(false);
  let error = $state("");
  let scrollEl = $state<HTMLElement | null>(null);
  let abortCtrl = $state<AbortController | null>(null);

  // slash-command palette (mirrors the TUI's command autocomplete)
  const COMMANDS = [
    { cmd: "/chat", desc: "grounded chat", view: "chat" },
    { cmd: "/live", desc: "live watch", view: "live" },
    { cmd: "/watch", desc: "monitor + narration", view: "watch" },
    { cmd: "/timeline", desc: "activity feed", view: "timeline" },
    { cmd: "/brief", desc: "evidence-cited recap", view: "brief" },
    { cmd: "/observe", desc: "attention board", view: "observe" },
    { cmd: "/since", desc: "what changed", view: "since" },
    { cmd: "/diff", desc: "structured delta", view: "diff" },
    { cmd: "/now", desc: "next step (drafts)", view: "drafts" },
    { cmd: "/goal", desc: "draft /goal (drafts)", view: "drafts" },
    { cmd: "/loop", desc: "draft /loop (drafts)", view: "drafts" },
    { cmd: "/handoff", desc: "shareable brief (drafts)", view: "drafts" },
    { cmd: "/fleet", desc: "multi-session board", view: "fleet" },
    { cmd: "/state", desc: "raw state JSON", view: "state" },
    { cmd: "/settings", desc: "backend & model", view: "settings" },
    { cmd: "/clear", desc: "clear this chat (in-memory)", action: "clear" },
    { cmd: "/new", desc: "start a new cockpit conversation", action: "new" },
    { cmd: "/forget", desc: "forget saved chat", action: "forget" },
    { cmd: "/stop", desc: "stop the stream", action: "stop" },
    { cmd: "/help", desc: "list commands", action: "help" },
  ];
  let slashIdx = $state(0);
  const filtered = $derived(draft.startsWith("/") ? COMMANDS.filter((c) => c.cmd.startsWith(draft)) : []);
  const slashOpen = $derived(draft.startsWith("/") && filtered.length > 0);

  function acceptCmd(c: typeof COMMANDS[number]) {
    draft = "";
    if (c.view) goto(c.view);
    else if (c.action === "clear") messages = [];
    else if (c.action === "new") newChat();
    else if (c.action === "forget") forget();
    else if (c.action === "stop") stop();
    // /help: leave the palette open (filtered shows all) by setting draft="/"
    else if (c.action === "help") draft = "/";
  }

  // load this session's saved cockpit conversation when it changes
  $effect(() => {
    if (!sessionPath) { messages = []; activeConvId = null; return; }
    (async () => {
      try {
        const hist = await surfaces.cockpitHistory(sessionPath, activeConvId ?? undefined);
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
    messages = [...messages, { role: "user", text: q }, { role: "assistant", text: "" }];
    const aiIdx = messages.length - 1;
    await scrollToBottom();
    busy = true;
    abortCtrl = new AbortController();
    try {
      await streamMethod(
        "chat_stream",
        { session: sessionPath, history, question: q, scope, scope_sessions: scopeSessions },
        (chunk) => {
          // Mutate through the proxied $state element so Svelte 5's deep
          // reactivity updates only this row - no per-chunk array copy / full
          // list re-render (the audit's chat re-render storm).
          messages[aiIdx].text += chunk;
          scrollToBottom();
        },
        abortCtrl.signal,
      );
      // persist the completed Q&A turn (best-effort)
      surfaces.cockpitRecord({ session: sessionPath, question: q, answer: messages[aiIdx].text, conv_id: activeConvId ?? undefined }).catch(() => {});
    } catch (e) {
      const err = e as Error;
      if (err.name === "AbortError") {
        // user stopped - keep the partial answer, no error toast
      } else {
        error = err.message || String(e);
      }
    } finally {
      busy = false;
      abortCtrl = null;
    }
  }

  function stop() { abortCtrl?.abort(); }

  async function forget() {
    if (!sessionPath || busy) return;
    try { await surfaces.cockpitForget(sessionPath, activeConvId ?? undefined); } catch { /* ignore */ }
    messages = [];
    activeConvId = null;
    error = "";
    toast("conversation cleared", "ok");
  }

  async function newChat() {
    if (!sessionPath || busy) return;
    busy = true;
    try {
      const created = await surfaces.cockpitNew(sessionPath);
      if (created) {
        activeConvId = created.conv_id;
        messages = [];
        toast("new cockpit conversation started", "ok");
      } else {
        toast("could not start new conversation", "error");
      }
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
      toast(error, "error");
    } finally { busy = false; }
  }

  function userMessageNumber(idx: number): number {
    let n = 0;
    for (let i = 0; i <= idx; i++) {
      if (messages[i].role === "user") n++;
    }
    return n;
  }

  async function rewind(idx: number) {
    if (!sessionPath || busy) return;
    busy = true;
    try {
      const n = userMessageNumber(idx);
      const hist = await surfaces.cockpitRewind({ session: sessionPath, message_index: n, conv_id: activeConvId ?? undefined });
      messages = hist.map(([r, t]) => ({ role: r as Message["role"], text: t }));
      toast(`rewound to before message ${n}`, "ok");
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
      toast(error, "error");
    } finally { busy = false; }
  }

  async function rewindUndo() {
    if (!sessionPath || busy) return;
    busy = true;
    try {
      const hist = await surfaces.cockpitRewindUndo(sessionPath, activeConvId ?? undefined);
      messages = hist.map(([r, t]) => ({ role: r as Message["role"], text: t }));
      toast("rewind undone", "ok");
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
      toast(error, "error");
    } finally { busy = false; }
  }

  async function scrollToBottom() {
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
  }

  function onKey(e: KeyboardEvent) {
    if (slashOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); slashIdx = Math.min(slashIdx + 1, filtered.length - 1); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); slashIdx = Math.max(slashIdx - 1, 0); return; }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault(); if (filtered[slashIdx]) acceptCmd(filtered[slashIdx]); return;
      }
      if (e.key === "Escape") { e.preventDefault(); draft = ""; return; }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  // keep the selection in range as the filter narrows
  $effect(() => { void filtered; if (slashIdx >= filtered.length) slashIdx = Math.max(0, filtered.length - 1); });

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
    {#each messages as m, i (m)}
      <div class="msg {m.role}">
        {#if m.role === "user"}
          <div class="bubble">{m.text}</div>
          <button class="rewind" onclick={() => rewind(i)} title="rewind to before this message" disabled={busy}>rewind</button>
        {:else}
          <div class="md">{@html render(m.text)}{#if busy && m === messages[messages.length - 1]}<span class="cursor">▋</span>{/if}</div>
        {/if}
      </div>
    {/each}
  </div>
  {#if error}<div class="error">⚠ {error}</div>{/if}
  <div class="composer">
    <div class="input-wrap">
      <textarea
        bind:value={draft}
        onkeydown={onKey}
        placeholder="Ask about this session... (type / for commands; Enter to send, Shift+Enter for newline)"
        disabled={busy}
        rows="2"
      ></textarea>
      {#if slashOpen}
        <div class="slash-palette">
          {#each filtered as c, i}
            <button class="slash-item" class:active={i === slashIdx} onclick={() => acceptCmd(c)} onmouseenter={() => (slashIdx = i)}>
              <span class="slash-cmd">{c.cmd}</span><span class="slash-desc">{c.desc}</span>
            </button>
          {/each}
        </div>
      {/if}
    </div>
    <button onclick={send} disabled={busy || !draft.trim() || !sessionPath}>
      {busy ? "…" : "send"}
    </button>
    {#if busy}
      <button class="stop" onclick={stop} title="stop the streaming answer">stop</button>
    {/if}
    {#if messages.length}
      <button class="clear" onclick={forget} disabled={busy} title="clear this session's saved cockpit conversation">clear</button>
      <button class="undo" onclick={rewindUndo} disabled={busy} title="undo the last rewind">undo rewind</button>
    {/if}
    <button class="new" onclick={newChat} disabled={busy || !sessionPath} title="start a new cockpit conversation">new</button>
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
  .rewind { font-size: 11px; color: var(--muted); background: transparent; border: none; cursor: pointer; margin-left: 6px; }
  .rewind:hover { color: var(--accent); }
  .rewind:disabled { opacity: 0.5; cursor: default; }
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
  .input-wrap { flex: 1; position: relative; }
  textarea {
    width: 100%; resize: none; font-family: inherit; font-size: 13px; line-height: 1.4;
    padding: 8px 10px; background: var(--panel); color: var(--text);
    border: 1px solid var(--border); border-radius: 8px; outline: none;
  }
  textarea:focus { border-color: var(--accent); }
  .slash-palette {
    position: absolute; bottom: calc(100% + 6px); left: 0; right: 0; z-index: 10;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.5); max-height: 240px; overflow: auto; padding: 4px;
  }
  .slash-item { display: flex; gap: 12px; width: 100%; text-align: left; padding: 7px 10px;
    background: transparent; color: var(--text); border: none; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .slash-item.active { background: var(--panel-2); }
  .slash-cmd { color: var(--accent); font-family: "SF Mono", ui-monospace, monospace; min-width: 90px; }
  .slash-desc { color: var(--muted); }
  button {
    padding: 8px 16px; font-size: 13px; font-weight: 500; cursor: pointer;
    background: var(--accent); color: #0f1115; border: none; border-radius: 8px;
  }
  button:disabled { opacity: 0.5; cursor: default; }
  .clear { background: transparent; color: var(--muted); border: 1px solid var(--border); }
  .undo { background: transparent; color: var(--accent); border: 1px solid var(--border); }
  .new { background: transparent; color: var(--good); border: 1px solid var(--border); }
  .stop { background: transparent; color: var(--bad); border: 1px solid var(--bad); }
</style>