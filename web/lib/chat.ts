// Ask the Data client (Task 19.2): POST a question, read the answer back as
// Server-Sent Events from the same response. `EventSource` only does GET,
// so this reads the fetch body stream directly. Cookies carry the session;
// the CSRF header is the same double-submit token every mutation sends.

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
      usage: {
        input_tokens: number;
        output_tokens: number;
        cache_read_tokens: number;
        cache_write_tokens: number;
        cycles: number;
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
  if (!response.ok) throw new ApiError(response.status, "delete_failed", "could not delete chat memory");
  const body = (await response.json()) as { deleted_events: number };
  return body.deleted_events;
}

export async function streamChat(
  sessionId: string,
  prompt: string,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json", Accept: "text/event-stream" };
  const csrf = csrfToken();
  if (csrf) headers["X-CSRF-Token"] = csrf;

  const response = await fetch(`${API_BASE}/api/v1/chat/messages`, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify({ session_id: sessionId, prompt }),
    signal,
  });
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      payload?.error?.code ?? "chat_failed",
      payload?.error?.message ?? `request failed with status ${response.status}`,
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data:")) {
          try {
            onEvent(JSON.parse(line.slice(5).trim()) as ChatEvent);
          } catch {
            // A malformed event is skipped rather than ending the answer.
          }
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export function newSessionId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
