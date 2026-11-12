/**
 * Typed client for approvals, tasks, notifications and the member agent list. Shapes mirror
 * apps/api/src/workmind/api/v1/work.py and api/v1/agents.py (`members_router`).
 */

import { apiJson } from "@/lib/api";

// --- Approvals ------------------------------------------------------------------------------

/** "approved" is transient: the action runs right after the claim and ends executed or failed. */
export type ApprovalStatus = "pending" | "approved" | "executed" | "failed" | "rejected" | "expired";

export interface ApprovalOrigin {
  /** Chat (research mode). */
  conversation_id?: string;
  run_id?: string;
  /** Workflow runs. */
  via?: string;
  workflow_run_id?: string;
  step?: string;
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
  result: { content?: string; data?: unknown; error?: string } | null;
  origin: ApprovalOrigin;
  created_at: string;
}

export interface DecisionBody {
  decision: "approve" | "reject";
  arguments?: Record<string, unknown>;
  note?: string;
}

// --- Tasks ----------------------------------------------------------------------------------

export type TaskStatus = "open" | "in_progress" | "done" | "cancelled";
export type TaskScope = "mine" | "queues" | "created";

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

export const workApi = {
  listApprovals: (status: "pending" | "all") => apiJson<Approval[]>(`/api/v1/approvals?status=${status}`),
  getApproval: (approvalId: string) => apiJson<Approval>(`/api/v1/approvals/${id(approvalId)}`),
  decide: (approvalId: string, body: DecisionBody) =>
    apiJson<Approval>(`/api/v1/approvals/${id(approvalId)}/decision`, { method: "POST", json: body }),

  listTasks: (scope: TaskScope) => apiJson<Task[]>(`/api/v1/tasks?scope=${scope}`),
  /** Assignee, creator, or someone in the task's team queue; anyone else gets a 404. */
  getTask: (taskId: string) => apiJson<Task>(`/api/v1/tasks/${id(taskId)}`),
  createTask: (body: TaskCreate) => apiJson<Task>("/api/v1/tasks", { method: "POST", json: body }),
  updateTask: (taskId: string, body: TaskUpdate) =>
    apiJson<Task>(`/api/v1/tasks/${id(taskId)}`, { method: "PATCH", json: body }),

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
  approval: (approvalId: string) => `work/approvals/one/${approvalId}`,
  tasks: "work/tasks",
  taskList: (scope: TaskScope) => `work/tasks/${scope}`,
  task: (taskId: string) => `work/tasks/one/${taskId}`,
  agents: "work/agents",
};
