/** Tasks: labels, the create form and its payload. Pure; components/work/tasks-view.tsx renders it. */

import { meetingsKeys } from "@/lib/meetings/api";
import { workKeys, type Task, type TaskCreate, type TaskScope, type TaskStatus } from "@/lib/work/api";

/**
 * Cached lists a change to a task makes stale: the task lists, and "My commitments" (a meeting's
 * action item shows its task's status). Both are on /tasks, and the cache does not refetch on
 * focus, so a list that changes a task must refresh both.
 */
export const TASK_CHANGE_KEYS: readonly string[] = [workKeys.tasks, meetingsKeys.commitments];

export const TASK_SCOPES: readonly { value: TaskScope; label: string; empty: string }[] = [
  { value: "mine", label: "Mine", empty: "Nothing assigned to you is open." },
  { value: "queues", label: "My team queues", empty: "Your teams' queues are empty: nothing is waiting to be picked up." },
  { value: "created", label: "Created by me", empty: "You haven't created any tasks yet." },
];

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  open: "Open",
  in_progress: "In progress",
  done: "Done",
  cancelled: "Cancelled",
};

export const TASK_STATUSES: readonly TaskStatus[] = ["open", "in_progress", "done", "cancelled"];

/** Who a task is for, from the viewer's point of view. Names come from the colleague directory (guests don't get it). */
export function assigneeText(
  task: Pick<Task, "assignee_id" | "department_id">,
  {
    meId,
    people,
    departments,
  }: {
    meId: string | null;
    people?: ReadonlyMap<string, { display_name: string }>;
    departments?: ReadonlyMap<string, { name: string }>;
  },
): string {
  if (task.assignee_id) {
    if (task.assignee_id === meId) return "You";
    return people?.get(task.assignee_id)?.display_name ?? "Someone else";
  }
  if (task.department_id) {
    const name = departments?.get(task.department_id)?.name;
    return name ? `${name} queue` : "A team queue";
  }
  return "Unassigned";
}

/** "Due tomorrow", "Overdue since 3 Nov", "Due 12 Nov". */
export function dueText(dueAt: string | null, now: Date = new Date()): { text: string; overdue: boolean } | null {
  if (!dueAt) return null;
  const due = new Date(dueAt);
  if (Number.isNaN(due.getTime())) return null;
  const day = (d: Date) => Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  const days = Math.round((day(due) - day(now)) / 86_400_000);
  const date = due.toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });
  if (days < 0) return { text: `Overdue since ${date}`, overdue: true };
  if (days === 0) return { text: "Due today", overdue: false };
  if (days === 1) return { text: "Due tomorrow", overdue: false };
  return { text: `Due ${date}`, overdue: false };
}

export type AssignTo = "me" | "queue" | "person" | "nobody";

export interface TaskForm {
  title: string;
  description: string;
  assignTo: AssignTo;
  /** Department slug, for "queue". */
  department: string;
  /** User id, for "person" (picked from the colleague directory). */
  personId: string;
  due: string;
}

export function emptyTaskForm(): TaskForm {
  return { title: "", description: "", assignTo: "me", department: "", personId: "", due: "" };
}

export type TaskFormErrors = Partial<Record<"title" | "department" | "personId" | "due", string>>;

export const TASK_LIMITS = { titleMax: 200, descriptionMax: 5000 } as const;

export function validateTaskForm(form: TaskForm): TaskFormErrors {
  const errors: TaskFormErrors = {};
  if (!form.title.trim()) errors.title = "Add a title.";
  else if (form.title.trim().length > TASK_LIMITS.titleMax) errors.title = `At most ${TASK_LIMITS.titleMax} characters.`;
  if (form.assignTo === "queue" && !form.department) errors.department = "Choose a team.";
  if (form.assignTo === "person" && !form.personId) errors.personId = "Choose a person.";
  if (form.due && !/^\d{4}-\d{2}-\d{2}$/.test(form.due)) errors.due = "Pick a date.";
  return errors;
}

export function toTaskCreate(form: TaskForm, meId: string): TaskCreate {
  const body: TaskCreate = { title: form.title.trim(), description: form.description.trim() };
  if (form.assignTo === "me") body.assignee_id = meId;
  if (form.assignTo === "person" && form.personId) body.assignee_id = form.personId;
  if (form.assignTo === "queue" && form.department) body.department = form.department;
  if (form.due) body.due = form.due;
  return body;
}

export interface TaskOrigin {
  kind: "assistant" | "workflow" | "meeting" | "research" | "document";
  label: string;
  /** Where it came from, when the viewer can go there. */
  href: `/${string}` | null;
}

/** Where a task came from (`origin.via`); null for tasks people created here. */
export function taskOrigin(task: Pick<Task, "origin">): TaskOrigin | null {
  const { via, meeting_id: meetingId, meeting_deleted: deleted, meeting_title: title } = task.origin;
  if (via === "meeting" && deleted === true) {
    // The meeting was deleted; the task stays, naming it without a link.
    const label = typeof title === "string" && title ? `From “${title}” (deleted meeting)` : "From a deleted meeting";
    return { kind: "meeting", label, href: null };
  }
  if (via === "meeting") {
    return { kind: "meeting", label: "From a meeting", href: typeof meetingId === "string" ? `/meetings/${encodeURIComponent(meetingId)}` : null };
  }
  if (via === "governance" && typeof task.origin.document_id === "string") {
    // A review reminder (docs/governance.md): the document to check and mark reviewed.
    return { kind: "document", label: "Open the document to review", href: `/knowledge/documents/${encodeURIComponent(task.origin.document_id)}` };
  }
  if (via === "connectors") {
    // A stale-document reminder (docs/connectors.md): the documents are listed in the task.
    return { kind: "document", label: "Stale synced documents", href: null };
  }
  if (via === "assistant") return { kind: "assistant", label: "From the assistant", href: null };
  if (via === "research") return { kind: "research", label: "From the assistant", href: null };
  if (via === "workflow") return { kind: "workflow", label: "From a workflow", href: null };
  return null;
}

export interface TaskDocument {
  id: string;
  title: string;
  reason: "not_updated" | "missing_upstream";
}

/**
 * The documents a stale-document reminder lists (origin.documents, written by
 * modules/connectors/reminders.py), and how many more it counted; empty for any other task.
 */
export function taskDocuments(task: Pick<Task, "origin">): { documents: TaskDocument[]; more: number } {
  const { via, documents, more } = task.origin;
  if (via !== "connectors" || !Array.isArray(documents)) return { documents: [], more: 0 };
  const listed: TaskDocument[] = [];
  for (const entry of documents as unknown[]) {
    if (typeof entry !== "object" || entry === null) continue;
    const { id, title, reason } = entry as Record<string, unknown>;
    if (typeof id !== "string" || typeof title !== "string") continue;
    listed.push({ id, title, reason: reason === "missing_upstream" ? "missing_upstream" : "not_updated" });
  }
  return { documents: listed, more: typeof more === "number" && more > 0 ? more : 0 };
}

/**
 * Whether the viewer can still open a task (the API's rule in work.py `_visible_task`: assignee,
 * creator, or a member of its team queue). Handing a task over can take it out of view.
 */
export function canSeeTask(
  task: Pick<Task, "assignee_id" | "created_by" | "department_id">,
  me: { id: string; departments: readonly { id: string }[] },
): boolean {
  if (task.assignee_id === me.id || task.created_by === me.id) return true;
  return task.department_id !== null && me.departments.some((d) => d.id === task.department_id);
}

/** Claiming takes an unassigned task from a team queue the viewer is in. */
export function canClaim(task: Pick<Task, "assignee_id" | "department_id" | "status">, me: { departments: readonly { id: string }[] }): boolean {
  const open = task.status === "open" || task.status === "in_progress";
  return open && task.assignee_id === null && task.department_id !== null && me.departments.some((d) => d.id === task.department_id);
}

/** Open tasks first (by due date, then newest), finished ones last. */
export function sortTasks(tasks: readonly Task[]): Task[] {
  const rank = (t: Task) => (t.status === "open" || t.status === "in_progress" ? 0 : 1);
  const due = (t: Task) => (t.due_at ? Date.parse(t.due_at) : Number.POSITIVE_INFINITY);
  return [...tasks].sort((a, b) => rank(a) - rank(b) || due(a) - due(b) || Date.parse(b.created_at) - Date.parse(a.created_at));
}
