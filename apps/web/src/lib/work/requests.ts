/**
 * Requests: the form that sends a request to whichever team handles it, and how a routing
 * decision reads (docs/automation.md, request routing). Pure; components/work/request-form.tsx
 * and the task pages render it.
 */

import type { RequestCreate, Task, TaskRouting } from "@/lib/work/api";

export const REQUEST_LIMITS = { titleMax: 200, descriptionMax: 5000 } as const;

export interface RequestForm {
  title: string;
  description: string;
  /** "" for none. */
  category: string;
  due: string;
}

export type RequestFormErrors = Partial<Record<"title" | "description" | "due", string>>;

export function emptyRequestForm(): RequestForm {
  return { title: "", description: "", category: "", due: "" };
}

export function validateRequestForm(form: RequestForm): RequestFormErrors {
  const errors: RequestFormErrors = {};
  if (!form.title.trim()) errors.title = "Say in a few words what you need.";
  else if (form.title.trim().length > REQUEST_LIMITS.titleMax) errors.title = `At most ${REQUEST_LIMITS.titleMax} characters.`;
  if (form.description.length > REQUEST_LIMITS.descriptionMax) errors.description = `At most ${REQUEST_LIMITS.descriptionMax} characters.`;
  if (form.due && !/^\d{4}-\d{2}-\d{2}$/.test(form.due)) errors.due = "Pick a date.";
  return errors;
}

export function toRequestCreate(form: RequestForm): RequestCreate {
  const body: RequestCreate = { title: form.title.trim(), description: form.description.trim() };
  if (form.category) body.category = form.category;
  if (form.due) body.due = form.due;
  return body;
}

export const ROUTING_METHOD_LABELS: Record<TaskRouting["method"], string> = {
  rule: "Routed by a rule",
  model: "Routed by the model",
  triage: "Waiting for a person to route it",
  human: "Routed by a person",
};

/** Where a request went, in a few words: "Finance" or "Triage". */
export function destination(routing: Pick<TaskRouting, "department" | "department_name">): string {
  return routing.department_name ?? routing.department ?? "Triage";
}

/** "Routed by the model (confidence 0.86)": the method, with the model's confidence. */
export function routingLine(routing: Pick<TaskRouting, "method" | "confidence">): string {
  const label = ROUTING_METHOD_LABELS[routing.method] ?? routing.method;
  return routing.method === "model" && routing.confidence != null ? `${label} (confidence ${routing.confidence.toFixed(2)})` : label;
}

/** Whether a task is a routed request (it has a routing decision to show). */
export function isRequest(task: Pick<Task, "routing">): task is Pick<Task, "routing"> & { routing: TaskRouting } {
  return task.routing != null;
}
