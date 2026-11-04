/** Tasks: labels, the create form and its payload. Pure; components/work/tasks-view.tsx renders it. */

import type { Task, TaskCreate, TaskScope, TaskStatus } from "@/lib/work/api";

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

/** Who a task is for, from the viewer's point of view. Names need the admin directory. */
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
  /** User id, for "person" (admins pick from the directory). */
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

/** Open tasks first (by due date, then newest), finished ones last. */
export function sortTasks(tasks: readonly Task[]): Task[] {
  const rank = (t: Task) => (t.status === "open" || t.status === "in_progress" ? 0 : 1);
  const due = (t: Task) => (t.due_at ? Date.parse(t.due_at) : Number.POSITIVE_INFINITY);
  return [...tasks].sort((a, b) => rank(a) - rank(b) || due(a) - due(b) || Date.parse(b.created_at) - Date.parse(a.created_at));
}
