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

export interface CandidateSource {
  kind: string;
  connector_id: string;
  item_id: string;
  revision: string;
  thread_id: string;
  subject: string | null;
  sender: string | null;
  received_at: string;
  excerpt: string | null;
  deep_link: string | null;
}

export interface Candidate {
  id: string;
  tenant_id: string;
  status: string;
  title: string;
  description: string | null;
  due_at: string | null;
  priority: string;
  confidence: number;
  source: CandidateSource;
  accepted_todo_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface CandidatePage {
  items: Candidate[];
  next_cursor: string | null;
}

export interface Todo {
  id: string;
  tenant_id: string;
  source_candidate_id: string;
  title: string;
  description: string | null;
  status: string;
  due_at: string | null;
  snoozed_until: string | null;
  completed_at: string | null;
  priority: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface TodoPage {
  items: Todo[];
  next_cursor: string | null;
}

export interface Notification {
  firing_id: string;
  schedule_id: string;
  schedule_name: string;
  channel: string;
  scheduled_for: string;
  status: string;
  delivery_outcome: string | null;
  settled_at: string | null;
}

export interface NotificationPage {
  items: Notification[];
  next_cursor: string | null;
}

export interface ApprovalPreviewDetail {
  label: string;
  value: string;
}

export interface ApprovalPreview {
  action: string;
  summary: string;
  details: ApprovalPreviewDetail[];
  risk: string | null;
}

export interface PendingApproval {
  correlation_id: string;
  tenant_id: string;
  run_id: string;
  session_id: string;
  invocation_id: string;
  tool_name: string;
  permission_scope: string;
  effect_class: string;
  policy_version: string;
  normalized_args_hash: string;
  human_readable_preview: ApprovalPreview;
  authorized_actor: { type: string; id: string };
  expires_at: string;
  requested_at: string;
}

export interface PendingApprovalPage {
  items: PendingApproval[];
  next_cursor: string | null;
}

export interface ActivityReceipt {
  id: string;
  receipt_type: string;
  actor_type: string;
  trigger_type: string;
  action: string;
  outcome: string;
  reversible: boolean;
  summary: Record<string, unknown>;
  run_id: string | null;
  subject_type: string | null;
  subject_id: string | null;
  occurred_at: string;
}

export interface ActivityPage {
  items: ActivityReceipt[];
  next_cursor: string | null;
}

export interface DeleteImportedResult {
  deleted: Record<string, number>;
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
  listCandidates: (status = "pending") =>
    req<CandidatePage>(`/candidates?status=${encodeURIComponent(status)}`),
  acceptCandidate: (csrf: string, id: string, ifVersion: number) =>
    req<{ candidate: Candidate; todo: Todo }>(
      `/candidates/${id}/accept`,
      jsonInit("POST", csrf, { if_version: ifVersion }),
    ),
  dismissCandidate: (csrf: string, id: string, ifVersion: number) =>
    req<Candidate>(
      `/candidates/${id}/dismiss`,
      jsonInit("POST", csrf, { if_version: ifVersion }),
    ),
  listTodos: () => req<TodoPage>("/todos"),
  patchTodo: (csrf: string, id: string, patch: Record<string, unknown>) =>
    req<Todo>(`/todos/${id}`, jsonInit("PATCH", csrf, patch)),
  listNotifications: () => req<NotificationPage>("/notifications"),
  listPermissions: () => req<PendingApprovalPage>("/permissions"),
  listActivity: (type?: string) =>
    req<ActivityPage>(`/activity${type ? `?type=${encodeURIComponent(type)}` : ""}`),
  deleteImported: (csrf: string) =>
    req<DeleteImportedResult>("/activity/delete-imported", jsonInit("POST", csrf)),
};

export function exportUrl(): string {
  return "/activity/export";
}

export function eventsUrl(sid: string, cursor: string | number): string {
  return `/sessions/${sid}/events?cursor=${cursor}`;
}
