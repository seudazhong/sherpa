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

export interface AppMeta {
  version: string;
  provider_kind: string;
  model: string;
  real_model: boolean;
}

export type ResumeState =
  | "ready"
  | "running"
  | "stale"
  | "approval"
  | "approval_expired"
  | "interrupted"
  | "effect_unknown"
  | "failed"
  | "archived";

export interface SessionMatch {
  kind: "title" | "user_message" | "assistant_message" | "tool" | "action";
  snippet: string;
  anchor_kind: "message" | "event" | "audit" | "session";
  anchor_id: string;
  additional_matches: number;
}

export interface SessionSummary {
  id: string;
  tenant_id: string;
  channel: string;
  umo_key: string;
  title: string | null;
  resume_state: ResumeState;
  latest_run_state: string | null;
  last_message_preview: string | null;
  last_activity_at: string | null;
  created_at: string;
  updated_at: string;
  match?: SessionMatch | null;
}

export interface SessionPage {
  items: SessionSummary[];
  next_cursor: string | null;
}

export interface ResumeStateResponse {
  session_id: string;
  resume_state: ResumeState;
  latest_run_state: string | null;
  live: boolean;
  pending_approval_id: string | null;
  unresolved_effect_id: string | null;
  events_url: string;
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

export interface Grant {
  id: string;
  tool_name: string;
  match_json: Record<string, unknown>;
  created_via: string;
  created_at: string;
}

export interface GrantPage {
  items: Grant[];
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

export interface Schedule {
  id: string;
  tenant_id: string;
  kind: string;
  name: string;
  todo_id: string | null;
  reminder_kind: string | null;
  delivery_channel: string;
  timezone: string;
  local_time: string | null;
  cadence_kind: string;
  cron_expr: string | null;
  interval_seconds: number | null;
  weekly_days: string | null;
  monthly_day: number | null;
  prompt: string | null;
  next_fire_at: string;
  last_fired_at: string | null;
  status: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ScheduleFiring {
  id: string;
  schedule_id: string;
  scheduled_for: string;
  status: string;
  delivery_outcome: string | null;
  run_id: string | null;
  settled_at: string | null;
  created_at: string;
}

export interface ScheduleFiringPage {
  items: ScheduleFiring[];
  next_cursor: string | null;
}

export interface SchedulePage {
  items: Schedule[];
  next_cursor: string | null;
}

export interface ApprovalEnvelopeBody {
  schema_version: "1.0";
  correlation_id: string;
  bound: { tenant_id: string; run_id: string; invocation_id: string };
  action: { tool_name: string; permission_scope: string; session_id: string };
  effect_class: string;
  normalized_args_hash: string;
  human_readable_preview: ApprovalPreview;
  policy_version: string;
  expires_at: string;
  nonce?: string;
  authorized_actor: { type: "user"; id: string };
  decision: {
    actor: { type: "user"; id: string };
    channel: "web";
    choice: string;
  };
}

export interface ApprovalResolution {
  correlation_id: string;
  state: "resolved";
  winning_decision: {
    actor: { type: string; id: string };
    channel: string;
    choice: string;
  };
  decided_at: string;
}

export interface Settings {
  notifications_enabled: boolean;
  web_enabled: boolean;
  email_digest_enabled: boolean;
  timezone: string;
  quiet_hours_enabled: boolean;
  quiet_hours_start: string;
  quiet_hours_end: string;
  daily_cap: number;
  version: number;
}

export interface MemoryItem {
  key: string;
  value: string;
  version: number;
}

export interface MemoryPage {
  items: MemoryItem[];
}

export interface PassageItem {
  id: string;
  text: string;
  source: string;
  created_at: string;
}

export interface PassagePage {
  items: PassageItem[];
}

export interface FileItem {
  id: string;
  path: string;
  size_bytes: number;
  content_type: string;
  version: number;
  updated_at: string;
}

export interface FilePage {
  items: FileItem[];
}

export interface DriveNode {
  id: string;
  parent_id: string | null;
  node_type: "folder" | "file";
  name: string;
  size_bytes: number;
  content_type: string;
  version: number;
  trashed: boolean;
  updated_at: string;
}

export interface DriveNodePage {
  items: DriveNode[];
  next_cursor: string | null;
}

export interface DriveVersion {
  version: number;
  size_bytes: number;
  content_type: string;
  created_at: string;
}

export interface StorageAccount {
  quota_bytes: number;
  used_bytes: number;
  reserved_bytes: number;
  trashed_bytes: number;
  available_bytes: number;
}

export interface QQStatus {
  enabled: boolean;
  configured: boolean;
  app_id: string;
  owner_openid_set: boolean;
  secret_set: boolean;
  webhook_path: string;
}

export interface QQTestResult {
  ok: boolean;
  detail: string;
}

export interface QQBindStart {
  task_id: string;
  qr_url: string;
}

export interface QQBindPollResult {
  status: string; // pending | completed | expired
  app_id: string;
  owner_openid: string;
}

export interface EmailStatus {
  kind: string;
  enabled: boolean;
  configured: boolean;
  inbox_id: string;
  owner_email: string;
  webhook_secret_set: boolean;
  webhook_path: string;
}

export interface ThreadSummary {
  session_id: string;
  channel: string;
  external_id: string;
  created_at: string;
}

export interface ChannelsStatus {
  qq: QQStatus;
  email: EmailStatus;
  threads: ThreadSummary[];
}

export interface SimulateResult {
  status: string;
  session_id: string | null;
  run_id: string | null;
  decision: string | null;
}

export interface ThreadMessage {
  role: string;
  text: string;
  at: string;
}

export interface PendingApprovalBrief {
  correlation_id: string;
  short_id: string;
  tool_name: string;
  summary: string;
}

export interface ThreadTranscript {
  session_id: string;
  channel: string;
  external_id: string;
  messages: ThreadMessage[];
  pending_approvals: PendingApprovalBrief[];
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
    throw new ApiError(
      res.status,
      `${init.method ?? "GET"} ${path} -> ${res.status}`,
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function jsonInit(
  method: string,
  csrf: string | null,
  body?: unknown,
): RequestInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  return {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export const api = {
  login: (email: string, password: string) =>
    req<AuthSession>(
      "/auth/login",
      jsonInit("POST", null, { email, password }),
    ),
  session: () => req<AuthSession>("/auth/session"),
  getMeta: () => req<AppMeta>("/meta"),
  logout: (csrf: string) => req<void>("/auth/logout", jsonInit("POST", csrf)),
  listSessions: (params?: {
    query?: string;
    status?: string;
    channel?: string;
    cursor?: string;
    limit?: number;
  }) => {
    const q = new URLSearchParams();
    if (params?.query) q.set("query", params.query);
    if (params?.status) q.set("status", params.status);
    if (params?.channel) q.set("channel", params.channel);
    if (params?.cursor) q.set("cursor", params.cursor);
    if (params?.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return req<SessionPage>(`/sessions${qs ? `?${qs}` : ""}`);
  },
  renameSession: (csrf: string, sid: string, title: string) =>
    req<SessionSummary>(
      `/sessions/${sid}/title`,
      jsonInit("PATCH", csrf, { title }),
    ),
  resumeState: (sid: string) =>
    req<ResumeStateResponse>(`/sessions/${sid}/resume-state`),
  sessionTimeline: (sid: string, anchorKind: string, anchorId: string) =>
    req<MessagePage>(
      `/sessions/${sid}/timeline?anchor_kind=${encodeURIComponent(anchorKind)}&anchor_id=${encodeURIComponent(anchorId)}`,
    ),
  recoverSession: (
    csrf: string,
    sid: string,
    action: "recheck" | "verified" | "new_run",
  ) =>
    req<ResumeStateResponse>(
      `/sessions/${sid}/recover`,
      jsonInit("POST", csrf, { action }),
    ),
  createSession: (csrf: string, title?: string | null) =>
    req<SessionSummary>(
      "/sessions",
      jsonInit("POST", csrf, { title: title ?? null }),
    ),
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
  resolvePermission: (
    csrf: string,
    p: PendingApproval,
    nonce: string | null,
    choice: string,
  ) =>
    req<ApprovalResolution>(
      `/permissions/${p.correlation_id}/resolve`,
      jsonInit("POST", csrf, {
        schema_version: "1.0",
        correlation_id: p.correlation_id,
        bound: {
          tenant_id: p.tenant_id,
          run_id: p.run_id,
          invocation_id: p.invocation_id,
        },
        action: {
          tool_name: p.tool_name,
          permission_scope: p.permission_scope,
          session_id: p.session_id,
        },
        effect_class: p.effect_class,
        normalized_args_hash: p.normalized_args_hash,
        human_readable_preview: p.human_readable_preview,
        policy_version: p.policy_version,
        expires_at: p.expires_at,
        ...(nonce ? { nonce } : {}),
        authorized_actor: { type: "user", id: p.authorized_actor.id },
        decision: {
          actor: { type: "user", id: p.authorized_actor.id },
          channel: "web",
          choice,
        },
      } satisfies ApprovalEnvelopeBody),
    ),
  listGrants: () => req<GrantPage>("/grants"),
  createGrant: (
    csrf: string,
    toolName: string,
    matchJson: Record<string, unknown>,
  ) =>
    req<Grant>(
      "/grants",
      jsonInit("POST", csrf, { tool_name: toolName, match_json: matchJson }),
    ),
  deleteGrant: (csrf: string, id: string) =>
    req<void>(`/grants/${id}`, jsonInit("DELETE", csrf)),
  listActivity: (type?: string) =>
    req<ActivityPage>(
      `/activity${type ? `?type=${encodeURIComponent(type)}` : ""}`,
    ),
  deleteImported: (csrf: string) =>
    req<DeleteImportedResult>(
      "/activity/delete-imported",
      jsonInit("POST", csrf),
    ),
  listSchedules: () => req<SchedulePage>("/schedules"),
  createDigest: (
    csrf: string,
    localTime: string,
    timezone: string,
    name?: string,
  ) =>
    req<Schedule>(
      "/schedules",
      jsonInit("POST", csrf, {
        kind: "daily_digest",
        name: name ?? "Daily digest",
        local_time: localTime,
        timezone,
        delivery_channel: "web",
      }),
    ),
  createReminder: (
    csrf: string,
    todoId: string,
    nextFireAt: string,
    reminderKind: string,
    timezone: string,
    name: string,
  ) =>
    req<Schedule>(
      "/schedules",
      jsonInit("POST", csrf, {
        kind: "todo_reminder",
        name,
        todo_id: todoId,
        reminder_kind: reminderKind,
        next_fire_at: nextFireAt,
        timezone,
        delivery_channel: "web",
      }),
    ),
  cancelSchedule: (csrf: string, id: string, ifVersion: number) =>
    req<Schedule>(
      `/schedules/${id}/cancel`,
      jsonInit("POST", csrf, { if_version: ifVersion }),
    ),
  createScheduledTask: (
    csrf: string,
    body: {
      name: string;
      prompt: string;
      cadence_kind: string;
      cron_expr?: string;
      interval_seconds?: number;
      local_time?: string;
      weekly_days?: string;
      monthly_day?: number;
      timezone: string;
      delivery_channel: string;
    },
  ) =>
    req<Schedule>(
      "/schedules",
      jsonInit("POST", csrf, { kind: "agent_task", ...body }),
    ),
  runScheduleNow: (csrf: string, id: string) =>
    req<ScheduleFiring>(`/schedules/${id}/run-now`, jsonInit("POST", csrf)),
  setScheduleStatus: (
    csrf: string,
    id: string,
    ifVersion: number,
    status: "active" | "paused",
  ) =>
    req<Schedule>(
      `/schedules/${id}/status`,
      jsonInit("POST", csrf, { if_version: ifVersion, status }),
    ),
  listScheduleFirings: (id: string) =>
    req<ScheduleFiringPage>(`/schedules/${id}/firings`),
  getSettings: () => req<Settings>("/settings"),
  updateSettings: (csrf: string, patch: Record<string, unknown>) =>
    req<Settings>("/settings", jsonInit("PATCH", csrf, patch)),
  listMemory: () => req<MemoryPage>("/memory"),
  setMemory: (csrf: string, key: string, value: string) =>
    req<MemoryItem>("/memory", jsonInit("PUT", csrf, { key, value })),
  deleteMemory: (csrf: string, key: string) =>
    req<void>(`/memory/${encodeURIComponent(key)}`, jsonInit("DELETE", csrf)),
  listPassages: () => req<PassagePage>("/memory/passages"),
  addPassage: (csrf: string, text: string) =>
    req<PassageItem>("/memory/passages", jsonInit("POST", csrf, { text })),
  deletePassage: (csrf: string, id: string) =>
    req<void>(
      `/memory/passages/${encodeURIComponent(id)}`,
      jsonInit("DELETE", csrf),
    ),
  listFiles: () => req<FilePage>("/files"),
  uploadFile: async (
    csrf: string,
    path: string,
    file: File,
  ): Promise<FileItem> => {
    const fd = new FormData();
    fd.append("path", path);
    fd.append("upload", file);
    const res = await fetch("/files", {
      method: "POST",
      credentials: "include",
      headers: { "X-CSRF-Token": csrf },
      body: fd,
    });
    if (!res.ok) throw new ApiError(res.status, `POST /files -> ${res.status}`);
    return (await res.json()) as FileItem;
  },
  deleteFile: (csrf: string, id: string) =>
    req<void>(`/files/${id}`, jsonInit("DELETE", csrf)),
  driveList: (params: {
    parent?: string | null;
    query?: string;
    sort?: string;
    cursor?: string;
    limit?: number;
    trashed?: boolean;
  }) => {
    const qs = new URLSearchParams();
    if (params.parent) qs.set("parent", params.parent);
    if (params.query) qs.set("query", params.query);
    if (params.sort) qs.set("sort", params.sort);
    if (params.cursor) qs.set("cursor", params.cursor);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.trashed) qs.set("trashed", "true");
    const q = qs.toString();
    return req<DriveNodePage>(`/drive/nodes${q ? `?${q}` : ""}`);
  },
  driveStorage: () => req<StorageAccount>("/drive/storage"),
  driveCreateFolder: (csrf: string, parentId: string | null, name: string) =>
    req<DriveNode>(
      "/drive/folders",
      jsonInit("POST", csrf, { parent_id: parentId, name }),
    ),
  driveUpload: async (
    csrf: string,
    parentId: string | null,
    file: File,
    name?: string,
  ): Promise<DriveNode> => {
    const fd = new FormData();
    if (name) fd.append("name", name);
    if (parentId) fd.append("parent_id", parentId);
    fd.append("upload", file);
    const res = await fetch("/drive/files", {
      method: "POST",
      credentials: "include",
      headers: { "X-CSRF-Token": csrf },
      body: fd,
    });
    if (!res.ok)
      throw new ApiError(res.status, `POST /drive/files -> ${res.status}`);
    return (await res.json()) as DriveNode;
  },
  driveRename: (csrf: string, id: string, ifVersion: number, name: string) =>
    req<DriveNode>(
      `/drive/nodes/${id}`,
      jsonInit("PATCH", csrf, { if_version: ifVersion, name }),
    ),
  driveMove: (
    csrf: string,
    id: string,
    ifVersion: number,
    parentId: string | null,
  ) =>
    req<DriveNode>(
      `/drive/nodes/${id}`,
      jsonInit("PATCH", csrf, { if_version: ifVersion, parent_id: parentId }),
    ),
  driveVersions: (id: string) =>
    req<DriveVersion[]>(`/drive/nodes/${id}/versions`),
  driveRestoreVersion: (csrf: string, id: string, version: number) =>
    req<DriveNode>(
      `/drive/nodes/${id}/restore-version`,
      jsonInit("POST", csrf, { version }),
    ),
  driveTrash: (csrf: string, id: string) =>
    req<DriveNode>(`/drive/nodes/${id}/trash`, jsonInit("POST", csrf)),
  driveRestore: (csrf: string, id: string) =>
    req<DriveNode>(`/drive/nodes/${id}/restore`, jsonInit("POST", csrf)),
  drivePurge: (csrf: string, id: string) =>
    req<void>(`/drive/nodes/${id}`, jsonInit("DELETE", csrf)),
  channelsStatus: () => req<ChannelsStatus>("/channels"),
  simulateQQ: (csrf: string, text: string, fromId?: string) =>
    req<SimulateResult>(
      "/channels/qq/simulate",
      jsonInit("POST", csrf, { text, from_id: fromId ?? "" }),
    ),
  simulateEmail: (csrf: string, text: string, fromId?: string) =>
    req<SimulateResult>(
      "/channels/email/simulate",
      jsonInit("POST", csrf, { text, from_id: fromId ?? "" }),
    ),
  threadTranscript: (sid: string) =>
    req<ThreadTranscript>(`/channels/threads/${sid}`),
  putQQConfig: (
    csrf: string,
    cfg: {
      app_id: string;
      enabled: boolean;
      owner_openid: string;
      secret: string;
    },
  ) => req<QQStatus>("/channels/qq/config", jsonInit("PUT", csrf, cfg)),
  testQQ: (csrf: string) =>
    req<QQTestResult>("/channels/qq/test", jsonInit("POST", csrf)),
  qqBindStart: (csrf: string) =>
    req<QQBindStart>("/channels/qq/bind/start", jsonInit("POST", csrf)),
  qqBindPoll: (csrf: string, taskId: string) =>
    req<QQBindPollResult>(
      "/channels/qq/bind/poll",
      jsonInit("POST", csrf, { task_id: taskId }),
    ),
};

export function exportUrl(): string {
  return "/activity/export";
}

export function fileDownloadUrl(id: string): string {
  return `/files/${id}/content`;
}

export function driveDownloadUrl(id: string): string {
  return `/drive/nodes/${id}/content`;
}

export function eventsUrl(sid: string, cursor: string | number): string {
  return `/sessions/${sid}/events?cursor=${cursor}`;
}
