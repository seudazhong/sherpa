// Typed client for the Sherpa backend (contracts/api.md). Same-origin in dev via
// the Vite proxy; cookies (HttpOnly session) ride automatically, CSRF is sent as
// a header on unsafe requests.

export interface AuthSession {
  user_id: string;
  tenant_id: string;
  email: string;
  csrf_token: string;
  expires_at: string;
}

export interface SessionSummary {
  id: string;
  tenant_id: string;
  channel: string;
  umo_key: string;
  title: string | null;
  latest_run_state: string | null;
  last_message_preview: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionPage {
  items: SessionSummary[];
  next_cursor: string | null;
}

export interface PromptAdmission {
  session_id: string;
  message_id: string;
  run_id: string;
  admitted_seq: number;
  state: string;
  event_cursor: string;
  events_url: string;
}

export interface MessagePart {
  kind: string;
  text: string;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  seq: number;
  role: "user" | "assistant";
  parts: MessagePart[];
  run_id: string | null;
  created_at: string;
}

export interface MessagePage {
  items: ChatMessage[];
  next_cursor: string | null;
  event_cursor: string;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, { credentials: "include", ...init });
  if (!res.ok) {
    throw new ApiError(res.status, `${init.method ?? "GET"} ${path} -> ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function jsonInit(method: string, csrf: string | null, body?: unknown): RequestInit {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  return {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export const api = {
  login: (email: string, password: string) =>
    req<AuthSession>("/auth/login", jsonInit("POST", null, { email, password })),
  session: () => req<AuthSession>("/auth/session"),
  logout: (csrf: string) => req<void>("/auth/logout", jsonInit("POST", csrf)),
  listSessions: () => req<SessionPage>("/sessions"),
  createSession: (csrf: string, title?: string | null) =>
    req<SessionSummary>("/sessions", jsonInit("POST", csrf, { title: title ?? null })),
  listMessages: (sid: string) => req<MessagePage>(`/sessions/${sid}/messages`),
  prompt: (csrf: string, sid: string, text: string) =>
    req<PromptAdmission>(
      `/sessions/${sid}/prompt`,
      jsonInit("POST", csrf, { client_message_id: crypto.randomUUID(), text }),
    ),
};

export function eventsUrl(sid: string, cursor: string | number): string {
  return `/sessions/${sid}/events?cursor=${cursor}`;
}
