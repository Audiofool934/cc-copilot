// A tiny global toast store (Svelte 5 runes $state in a .svelte.ts module).
// Components call toast(msg) to flash a transient notification; the ToastRack
// component renders `toasts` reactively.

export interface Toast { id: number; msg: string; kind: "info" | "error" | "ok"; }

export const toasts = $state<Toast[]>([]);
let seq = 0;

export function toast(msg: string, kind: Toast["kind"] = "info", ttl = 3200) {
  const id = ++seq;
  toasts.push({ id, msg, kind });
  setTimeout(() => {
    const i = toasts.findIndex((t) => t.id === id);
    if (i >= 0) toasts.splice(i, 1);
  }, ttl);
}