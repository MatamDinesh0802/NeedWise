/**
 * Typed client for approvals, tasks, notifications and the member agent list. Shapes mirror
 * apps/api/src/workmind/api/v1/work.py and api/v1/agents.py (`members_router`).
 */

import { apiJson } from "@/lib/api";

// --- Approvals ------------------------------------------------------------------------------

/**
 * "approved" is transient: the action runs right after the claim and ends executed or failed.
 * "superseded": the same request also went to other approvers, and one of them decided it.
 * "forwarded": approved at one step of a chain; the next step's approvers decide now.
 */
export type ApprovalStatus = "pending" | "approved" | "executed" | "failed" | "rejected" | "expired" | "superseded" | "forwarded";

export interface ApprovalOrigin {
  /** Chat (research mode). */
  conversation_id?: string;
  run_id?: string;
  /** Workflow runs (`workflow`: its key, recorded since the links fix). */
  via?: string;
  workflow?: string;
  workflow_run_id?: string;
  step?: string;
  /** Knowledge governance (via "governance"): an official-status request for a document. */
  kind?: string;
  document_id?: string;
  request_id?: string;
  [key: string]: unknown;
}

export interface Approval {
  id: string;
  tool: string;
  summary: string;
  arguments: Record<string, unknown>;
  edited_arguments: Record<string, unknown> | null;
  status: ApprovalStatus;
  requested_by: string;
  approver_id: string;
  decided_at: string | null;
  decision_note: string | null;
  expires_at: string;
  /**
   * `reason`: why a copy was closed without a decision (e.g. the collection's owner changed).
   * `decided_in`: the copy whose decision answered the request. `next`, `stage`, `stages`: a
   * forwarded step's next copies (docs/automation.md, approver rules).
   */
  result: {
    content?: string;
    data?: unknown;
    error?: string;
    reason?: string;
    decided_in?: string;
    next?: string[];
    stage?: number;
    stages?: number;
  } | null;
  origin: ApprovalOrigin;
  created_at: string;
  /** This copy's step of the request (1-based) and how many steps it has. */
  stage?: number;
  stages?: number;
  /** The approver rule in words, e.g. "a manager of the requester's department, then …". */
  rule?: string | null;
  /** Set on a copy an escalation added: the copy whose approver had not decided in time. */
  escalated_from?: string | null;
}

export interface DecisionBody {
  decision: "approve" | "reject";
  arguments?: Record<string, unknown>;
  note?: string;
}

// --- Tasks ----------------------------------------------------------------------------------

export type TaskStatus = "open" | "in_progress" | "done" | "cancelled";
/** "triage": requests nobody could route with confidence (for org admins and triagers). */
export type TaskScope = "mine" | "queues" | "created" | "triage";

/** How a request reached its queue, and why (modules/work/routing.py). */
export interface TaskRouting {
  method: "rule" | "model" | "triage" | "human";
  department: string | null;
  department_name: string | null;
  confidence: number | null;
  matched: string[];
  explanation: string;
  source?: "form" | "email" | "assistant";
}

export interface Task {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  assignee_id: string | null;
  department_id: string | null;
  created_by: string | null;
  due_at: string | null;
  origin: Record<string, unknown>;
  created_at: string;
  routing?: TaskRouting | null;
  /** Waiting in triage for a person to route it. */
  triage?: boolean;
}

export interface TaskCreate {
  title: string;
  description: string;
  assignee_id?: string;
  /** Department slug: the task goes to that team's queue. */
  department?: string;
  /** YYYY-MM-DD; due at 17:00 UTC. */
  due?: string;
}

export interface TaskUpdate {
  status?: TaskStatus;
  assignee_id?: string;
}

// --- Requests (routed to a department queue) -------------------------------------------------

export interface RequestOptions {
  /** Categories the admins set up (Admin › Automation): choosing one routes by its rule. */
  categories: string[];
  departments: { key: string; name: string }[];
}

export interface RequestCreate {
  title: string;
  description: string;
  category?: string;
  /** YYYY-MM-DD; due at 17:00 UTC. */
  due?: string;
}

export interface RequestRouting {
  method: TaskRouting["method"];
  department: string | null;
  department_name: string | null;
  confidence: number | null;
  matched: string[];
  explanation: string;
}

export interface RequestResult {
  task: Task;
  routing: RequestRouting;
}

// --- Notifications --------------------------------------------------------------------------

export interface Notification {
  id: string;
  kind: string;
  title: string;
  body: string;
  link: string | null;
  read_at: string | null;
  created_at: string;
}

// --- Agents (everyone) ----------------------------------------------------------------------

export interface AgentSummary {
  key: string;
  name: string;
  description: string;
  examples: string[];
}

const id = (value: string) => encodeURIComponent(value);

/** Narrows the list to one conversation's or one workflow run's approvals (still only mine). */
export interface ApprovalFilters {
  conversationId?: string;
  workflowRunId?: string;
}

export function approvalsPath(status: "pending" | "all", filters: ApprovalFilters = {}): string {
  const params = new URLSearchParams({ status });
  if (filters.conversationId) params.set("conversation_id", filters.conversationId);
  if (filters.workflowRunId) params.set("workflow_run_id", filters.workflowRunId);
  return `/api/v1/approvals?${params.toString()}`;
}

export const workApi = {
  listApprovals: (status: "pending" | "all", filters?: ApprovalFilters) => apiJson<Approval[]>(approvalsPath(status, filters)),
  getApproval: (approvalId: string) => apiJson<Approval>(`/api/v1/approvals/${id(approvalId)}`),
  decide: (approvalId: string, body: DecisionBody) =>
    apiJson<Approval>(`/api/v1/approvals/${id(approvalId)}/decision`, { method: "POST", json: body }),

  listTasks: (scope: TaskScope) => apiJson<Task[]>(`/api/v1/tasks?scope=${scope}`),
  /** Assignee, creator, or someone in the task's team queue; anyone else gets a 404. */
  getTask: (taskId: string) => apiJson<Task>(`/api/v1/tasks/${id(taskId)}`),
  createTask: (body: TaskCreate) => apiJson<Task>("/api/v1/tasks", { method: "POST", json: body }),
  updateTask: (taskId: string, body: TaskUpdate) =>
    apiJson<Task>(`/api/v1/tasks/${id(taskId)}`, { method: "PATCH", json: body }),
  /** Triagers (a request in triage) and queue members (an unclaimed request in their queue). */
  routeTask: (taskId: string, department: string, note = "") =>
    apiJson<Task>(`/api/v1/tasks/${id(taskId)}/route`, { method: "POST", json: { department, note } }),

  requestOptions: () => apiJson<RequestOptions>("/api/v1/requests/options"),
  createRequest: (body: RequestCreate) => apiJson<RequestResult>("/api/v1/requests", { method: "POST", json: body }),

  listNotifications: () => apiJson<Notification[]>("/api/v1/notifications"),
  /** Omit `ids` to mark everything read. */
  markRead: (ids?: string[]) =>
    apiJson<void>("/api/v1/notifications/read", { method: "POST", json: ids ? { ids } : {} }),

  listAgents: () => apiJson<AgentSummary[]>("/api/v1/agents"),
};

/** Query-cache keys. Everything under "work/approvals" is refreshed after a decision. */
export const workKeys = {
  approvals: "work/approvals",
  approvalList: (status: "pending" | "all") => `work/approvals/list/${status}`,
  /** Every approval proposed in one conversation (pending and decided). */
  conversationApprovals: (conversationId: string) => `work/approvals/conversation/${conversationId}`,
  approval: (approvalId: string) => `work/approvals/one/${approvalId}`,
  tasks: "work/tasks",
  taskList: (scope: TaskScope) => `work/tasks/${scope}`,
  task: (taskId: string) => `work/tasks/one/${taskId}`,
  agents: "work/agents",
  requestOptions: "work/requests/options",
};
