// Durable background jobs. Only opaque conversation IDs belong in browser
// storage; prompts, answers, and replay cursors live in memory or on the server.

import { ApiError, csrfToken } from "./api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type ChatEvent =
  | { type: "status"; status: string; publication_id?: string }
  | { type: "text"; delta: string }
  | { type: "tool"; id: string; name?: string; label?: string; status: "running" | "done" | "error" }
  | { type: "chart"; title: string; spec: Record<string, unknown> }
  | { type: "image"; title: string; src: string }
  | {
      type: "code";
      language: string;
      code: string;
      sql: string | null;
      stdout: string;
      stderr: string;
      ok: boolean;
    }
  | { type: "follow_ups"; questions: string[] }
  | { type: "final"; markdown: string }
  | {
      type: "complete";
      stop_reason: string;
      publication_id: string;
      model_id: string | null;
      partial?: boolean;
      usage: {
        input_tokens: number | null;
        output_tokens: number | null;
        cache_read_tokens: number | null;
        cache_write_tokens: number | null;
        cycles: number | null;
        partial?: boolean;
      };
    }
  | { type: "error"; message: string };

export interface ChatStatus {
  enabled: boolean;
  access: boolean;
  available: boolean;
}


export async function chatStatus(): Promise<ChatStatus> {
  const response = await fetch(`${API_BASE}/api/v1/chat/status`, { credentials: "include" });
  if (!response.ok) throw new ApiError(response.status, "status_failed", "could not load chat status");
  return (await response.json()) as ChatStatus;
}

export async function deleteChatMemory(): Promise<number> {
  const headers: Record<string, string> = {};
  const csrf = csrfToken();
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(`${API_BASE}/api/v1/chat/memory`, {
    method: "DELETE",
    credentials: "include",
    headers,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body?.error?.code ?? "delete_failed",
      body?.error?.message ?? "could not delete chat memory",
    );
  }
  return body.deleted_events;
}

export interface ChatJob {
  job_id: string;
  session_id: string;
  prompt: string;
  state: "pending" | "running" | "completed" | "failed" | "cancelled";
  created_at: number | string;
  updated_at: number | string;
  publication_id: string | null;
  error: string | null;
  next_cursor: number;
}

export interface ChatJobPage {
  job: ChatJob;
  events: ChatEvent[];
  next_cursor: number;
  has_more: boolean;
}

async function jobRequest<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const csrf = csrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const response = await fetch(`${API_BASE}/api/v1/chat/jobs${path}`, {
    method,
    credentials: "include",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
    cache: "no-store",
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      payload?.error?.code ?? "chat_failed",
      payload?.error?.message ?? `request failed with status ${response.status}`,
    );
  }
  if (payload === null) throw new TypeError("Invalid chat response");
  return payload as T;
}

export function createChatJob(sessionId: string, prompt: string, jobId: string, signal?: AbortSignal): Promise<ChatJob> {
  return jobRequest("", "POST", { session_id: sessionId, prompt, job_id: jobId }, signal);
}

export function listChatJobs(sessionId: string, signal?: AbortSignal): Promise<{ jobs: ChatJob[] }> {
  return jobRequest(`?session_id=${encodeURIComponent(sessionId)}`, "GET", undefined, signal);
}

export function getChatJob(jobId: string, after = 0, signal?: AbortSignal): Promise<ChatJobPage> {
  return jobRequest(`/${encodeURIComponent(jobId)}?after=${after}`, "GET", undefined, signal);
}

export function cancelChatJob(jobId: string, signal?: AbortSignal): Promise<ChatJob> {
  return jobRequest(`/${encodeURIComponent(jobId)}/cancel`, "POST", undefined, signal);
}

export function isActiveJob(job: Pick<ChatJob, "state">): boolean {
  return job.state === "pending" || job.state === "running";
}

export function compareChatJobs(a: ChatJob, b: ChatJob): number {
  const time = (value: number | string) => typeof value === "number" ? value * 1000 : Date.parse(value);
  return time(a.created_at) - time(b.created_at) || a.job_id.localeCompare(b.job_id);
}

export function isTransientChatError(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status === 408 || error.status === 429 || error.status >= 500;
}

export function chatPollDelay(failures: number): number {
  return Math.min(30_000, 1000 * 2 ** failures);
}

export function waitForChatPoll(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      reject(new DOMException("Stopped watching", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

export async function watchChatJob(
  jobId: string,
  onPage: (page: ChatJobPage) => void,
  onConnection: (reconnecting: boolean) => void,
  signal: AbortSignal,
  after = 0,
): Promise<void> {
  let cursor = after;
  let failures = 0;
  while (!signal.aborted) {
    try {
      const page = await getChatJob(jobId, cursor, signal);
      if (signal.aborted) return;
      onPage(page);
      cursor = page.next_cursor;
      failures = 0;
      onConnection(false);
      // Terminal jobs can still have multiple pages of events to replay.
      if (page.has_more) continue;
      if (!isActiveJob(page.job)) return;
    } catch (error) {
      if (signal.aborted) return;
      if (!isTransientChatError(error)) throw error;
      failures++;
      onConnection(true);
    }
    await waitForChatPoll(chatPollDelay(failures), signal);
  }
}

export function conversationStorageKey(actorId: string): string {
  return `xc-chat-session:${encodeURIComponent(actorId)}`;
}

export function savedConversationId(storage: Pick<Storage, "getItem" | "setItem">, actorId: string): string {
  const key = conversationStorageKey(actorId);
  const saved = storage.getItem(key);
  if (saved && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(saved)) return saved;
  const id = newSessionId();
  storage.setItem(key, id);
  return id;
}

export function stopReasonLabel(reason: string): string | null {
  const limits: Record<string, string> = {
    limit_time: "stopped at the time limit",
    limit_max_tokens: "stopped at the output-token limit",
    limit_tokens: "stopped at the token limit",
    limit_cycles: "stopped at the cycle limit",
    limit_iterations: "stopped at the iteration limit",
    limit_steps: "stopped at the step limit",
  };
  return limits[reason] ?? (reason.startsWith("limit") ? `stopped (${reason})` : null);
}

export function newSessionId(): string {
  return crypto.randomUUID();
}
