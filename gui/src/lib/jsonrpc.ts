// Typed JSON-RPC 2.0 client for the cc-copilot local server (cccopilot/server.py).
// The Tauri shell spawns `cc-copilot serve` and exposes its port via the
// `server_port` command; this client resolves that port once, then drives the
// facade over plain HTTP JSON-RPC at http://127.0.0.1:<port>/.

import { invoke } from "@tauri-apps/api/core";

let _port: number | null = null;

async function port(): Promise<number> {
  if (_port != null) return _port;
  _port = await invoke<number>("server_port");
  if (!_port) throw new Error("cc-copilot server is not available");
  return _port;
}

export interface RpcErrorPayload {
  code: number;
  message: string;
  data?: unknown;
}
interface RpcResponse<T> {
  result?: T;
  error?: RpcErrorPayload;
}

export class RpcException extends Error {
  code: number;
  data?: unknown;
  constructor(e: RpcErrorPayload) {
    super(e.message);
    this.code = e.code;
    this.data = e.data;
  }
}

export async function rpc<T>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  const p = await port();
  const res = await fetch(`http://127.0.0.1:${p}/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const json = (await res.json()) as RpcResponse<T>;
  if (json.error) throw new RpcException(json.error);
  return json.result as T;
}

// ---- surface types (mirror cccopilot/serialize.py) ----

export interface SessionRef {
  path: string;
  session_id: string;
  mtime: number;
  size: number;
  title: string;
  own: boolean;
  agent: string;
  model: string;
  live: boolean;
  nickname: string;
  forked_from: string;
  hhmm: string;
}

export interface TranscriptRecord {
  line: number;
  kind: string;
  ts: string | null;
  hhmm: string;
  raw_ts: string;
  text: string;
  tool_id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  is_error: boolean;
  level: string;
  housekeeping: boolean;
}

export interface Transcript {
  path: string;
  session_id: string;
  cwd: string;
  git_branch: string;
  version: string;
  permission_mode: string;
  title: string;
  title_is_custom: boolean;
  raw_lines: number;
  parse_errors: number;
  first_seen_ts: string | null;
  last_seen_ts: string | null;
  token_usage: Record<string, unknown> | null;
  records: TranscriptRecord[];
}

export interface AssessmentSignal {
  kind: string;
  severity: string;
  message: string;
  evidence: number[];
}
export interface Assessment {
  verdict: string;
  headline: string;
  signals: AssessmentSignal[];
}
export interface Intent {
  line: number;
  ts: string;
  text: string;
}
export interface FileChange {
  path: string;
  edits: number;
  writes: number;
  last_line: number;
  last_hhmm: string;
}
export interface Command {
  cmd: string;
  status: string;
  line: number;
  result_line: number | null;
  hhmm: string;
}
export interface Failure {
  tool: string;
  summary: string;
  line: number;
  call_line: number | null;
  hhmm: string;
}
export interface DiffBlock {
  new_events: number;
  status_from: string;
  status_to: string;
  verdict_from: string;
  verdict_to: string;
  new_failures: Failure[];
  new_changed: FileChange[];
}
export interface DiffView {
  cutoff_line: number;
  label: string;
  new_events: number;
  nothing_new: boolean;
  pending_ask: string;
  new_humans: TranscriptRecord[];
  new_agent: TranscriptRecord[];
  new_commands: Command[];
  new_failures: Failure[];
  new_changed_files: FileChange[];
  diff: DiffBlock | null;
  text: string;
  message?: string;
}
export interface BackendInfo {
  name: string;
  available: boolean;
  reason: string;
  active: boolean;
  needs_key: boolean;
  key_env: string;
  default_model: string;
}
export interface ModelInfo {
  id: string;
  note: string;
}
export interface State {
  assessment: Assessment;
  session_id: string;
  cwd: string;
  git_branch: string;
  version: string;
  permission_mode: string;
  events: number;
  status: string;
  idle_seconds: number | null;
  duration_seconds: number | null;
  tool_counts: Record<string, number>;
  intents: Intent[];
  todos: unknown[];
  changed_files: FileChange[];
  commands: Command[];
  failures: Failure[];
  pending_tool: { line: number; tool: string } | null;
}

// ---- convenience wrappers for the reading surfaces ----

export const surfaces = {
  projects: () => rpc<[string, number, number][]>("projects", {}),
  sessions: (cwd: string, include_current = false) =>
    rpc<SessionRef[]>("sessions", { cwd, include_current }),
  currentSessionPath: () => rpc<string | null>("current_session_path", {}),
  resolve: (cwd: string, session: string | null = null) =>
    rpc<string | null>("resolve", { cwd, session }),
  brief: (params: { cwd?: string; session?: string; scope?: string; scope_sessions?: string } = {}) =>
    rpc<string>("brief", params as Record<string, unknown>),
  check: (params: { cwd?: string; session?: string; scope?: string; scope_sessions?: string } = {}) =>
    rpc<string>("check", params as Record<string, unknown>),
  checkVerdict: (params: { cwd?: string; session?: string; scope?: string; scope_sessions?: string } = {}) =>
    rpc<number>("check_verdict", params as Record<string, unknown>),
  observe: (params: { cwd?: string; session?: string; scope?: string; scope_sessions?: string } = {}) =>
    rpc<string>("observe", params as Record<string, unknown>),
  since: (params: { cwd?: string; session?: string; when?: string; peek?: boolean } = {}) =>
    rpc<string>("since", params as Record<string, unknown>),
  diff: (params: { cwd?: string; session?: string; when?: string; peek?: boolean } = {}) =>
    rpc<DiffView>("diff", params as Record<string, unknown>),
  state: (path: string) => rpc<State>("state", { path }),
  transcript: (path: string) => rpc<Transcript>("transcript", { path }),
  advanceSinceMark: (params: { cwd?: string; session?: string } = {}) =>
    rpc<Record<string, unknown> | null>("advance_since_mark", params as Record<string, unknown>),
  status: (params: { cwd?: string; limit?: number; show_all?: boolean } = {}) =>
    rpc<string>("status", params as Record<string, unknown>),
  target: (params: { cwd?: string; session?: string; scope?: string; scope_sessions?: string } = {}) =>
    rpc<TargetInfo>("target", params as Record<string, unknown>),
  // ---- narration (LLM), blocking request/response ----
  now: (params: { cwd?: string; session?: string; instruction?: string; raw?: boolean } = {}) =>
    rpc<string>("now", params as Record<string, unknown>),
  goal: (params: { cwd?: string; session?: string; instruction?: string; raw?: boolean } = {}) =>
    rpc<string>("goal", params as Record<string, unknown>),
  loop: (params: { cwd?: string; session?: string; instruction?: string; raw?: boolean } = {}) =>
    rpc<string>("loop", params as Record<string, unknown>),
  recapSince: (params: { cwd?: string; session?: string; when?: string; instruction?: string } = {}) =>
    rpc<string>("recap_since", params as Record<string, unknown>),
  handoff: (params: { cwd?: string; session?: string } = {}) =>
    rpc<string>("handoff", params as Record<string, unknown>),
  watchProgress: (params: { delta_text: string; instruction?: string }) =>
    rpc<string>('watch_progress', params as Record<string, unknown>),
  // ---- cockpit session persistence ----
  cockpitHistory: (session: string) =>
    rpc<[string, string][]>("cockpit_history", { session }),
  cockpitRecord: (params: { session: string; question: string; answer: string }) =>
    rpc<number>("cockpit_record", params as Record<string, unknown>),
  cockpitForget: (session: string) =>
    rpc<boolean>("cockpit_forget", { session }),
  cockpitSessions: (cwd?: string) =>
    rpc<Record<string, unknown>[]>('cockpit_sessions', cwd ? { cwd } : {}),
  // ---- settings: backends / models ----
  backends: () => rpc<BackendInfo[]>('backends', {}),
  modelsFor: (name: string) => rpc<ModelInfo[]>('models_for', { name }),
  setBackend: (params: { name: string; model?: string; key?: string }) =>
    rpc<string>('set_backend', params as Record<string, unknown>),
  needsOnboarding: () => rpc<boolean>('needs_onboarding', {}),
  onboardChoices: (featured_only = true) =>
    rpc<OnboardChoice[]>('onboard_choices', { featured_only }),
  // ---- saved evidence scope groups ----
  scopeGroups: () => rpc<ScopeGroup[]>('scope_groups', {}),
  scopeGroupSave: (params: { name: string; scope?: string; scope_sessions?: string }) =>
    rpc<ScopeGroup>('scope_group_save', params as Record<string, unknown>),
  scopeGroupLoad: (name: string) => rpc<ScopeGroup | null>('scope_group_load', { name }),
  scopeGroupDelete: (name: string) => rpc<boolean>('scope_group_delete', { name }),
};
export interface OnboardChoice {
  name: string;
  label: string;
  kind: string;
  blurb: string;
  key_env: string;
  default_model: string;
  featured: boolean;
  brand_hex: string;
  ready: boolean;
  status: string;
}
export interface ScopeGroup {
  name: string;
  scope: string;
  scope_sessions: string[];
  updated_at?: number;
}
export interface TargetInfo {
  conv_id: string;
  path: string;
  scope: string;
  scope_sessions: string[];
  banner: string;
}
