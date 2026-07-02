// Streaming client for the cc-copilot server's POST /stream SSE endpoint.
// Drains a facade *_stream method chunk-by-chunk: emits `data: {"chunk": ...}`
// per chunk, then `event: done` with the final text, or `event: error`.

import { invoke } from "@tauri-apps/api/core";

let _port: number | null = null;

async function port(): Promise<number> {
  if (_port != null) return _port;
  _port = await invoke<number>("server_port");
  if (!_port) throw new Error("cc-copilot server is not available");
  return _port;
}

export interface StreamResult {
  text: string;
  usage?: unknown;
}

export async function streamMethod(
  method: string,
  params: Record<string, unknown>,
  onChunk: (chunk: string) => void,
  signal?: AbortSignal,
): Promise<StreamResult> {
  const p = await port();
  const res = await fetch(`http://127.0.0.1:${p}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ method, params }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`stream failed: HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let text = "";
  let usage: unknown;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const ev = parseEvent(block);
      if (!ev) continue;
      if (ev.event === "error") throw new Error(ev.data?.message ?? "stream error");
      if (ev.event === "done") {
        text = ev.data?.text ?? text;
        usage = ev.data?.usage;
      } else if (typeof ev.data?.chunk === "string") {
        text += ev.data.chunk;
        onChunk(ev.data.chunk);
      }
    }
  }
  return { text, usage };
}

function parseEvent(block: string): { event: string; data: any } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7);
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("")) };
  } catch {
    return null;
  }
}