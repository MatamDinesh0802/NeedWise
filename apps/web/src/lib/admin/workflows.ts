/**
 * Admin → Workflows. Shapes mirror apps/api/src/workmind/api/v1/work.py (admin_router) and
 * modules/work/workflows.py (Definition, Step). The editor takes the definition as JSON; the API
 * validates it and answers 422 `invalid_workflow` with Pydantic's message, shown per step.
 */

import { apiJson } from "@/lib/api";
import { describePath, detailErrors, type DetailError } from "@/lib/validation-detail";

export interface WorkflowStep {
  id: string;
  research?: string;
  tool?: string;
  arguments?: Record<string, unknown>;
  approver?: string;
}

export interface WorkflowDefinition {
  description?: string;
  trigger?: { schedule?: string | null; event?: string | null; collection?: string | null };
  steps: WorkflowStep[];
}

export interface Workflow {
  id: string;
  key: string;
  name: string;
  definition: WorkflowDefinition;
  enabled: boolean;
  owner_id: string;
}

/** "queued": an event created it and it hasn't started yet (it does within a minute). */
export type RunStatus = "queued" | "running" | "waiting_approval" | "succeeded" | "failed" | "cancelled";

export interface StepOutput {
  status?: string;
  text?: string;
  data?: Record<string, unknown>;
  error?: string;
  approval_id?: string;
  [key: string]: unknown;
}

export interface WorkflowRun {
  id: string;
  status: RunStatus;
  trigger: string;
  steps: Record<string, StepOutput>;
  current_step: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface WorkflowSave {
  key: string;
  name: string;
  definition: Record<string, unknown>;
  enabled: boolean;
}

const id = (value: string) => encodeURIComponent(value);

export const workflowsApi = {
  list: () => apiJson<Workflow[]>("/api/v1/admin/workflows"),
  save: (body: WorkflowSave) => apiJson<Workflow>(`/api/v1/admin/workflows/${id(body.key)}`, { method: "PUT", json: body }),
  run: (key: string) => apiJson<WorkflowRun>(`/api/v1/admin/workflows/${id(key)}/run`, { method: "POST" }),
  runs: (key: string) => apiJson<WorkflowRun[]>(`/api/v1/admin/workflows/${id(key)}/runs`),
  /** 409 runs_in_progress while a run is queued, running or waiting for an approval. */
  remove: (key: string) => apiJson<void>(`/api/v1/admin/workflows/${id(key)}`, { method: "DELETE" }),
};

export const workflowsKeys = {
  all: "admin/workflows",
  list: "admin/workflows/list",
  runs: (key: string) => `admin/workflows/runs/${key}`,
};

/** The example from docs/automation.md: research, then an email that waits for Olivia. */
export const WEEKLY_OPS_TEMPLATE = {
  key: "weekly-ops-report",
  name: "Weekly operations report",
  definition: {
    description: "Every Monday: a summary of last week's operations, emailed to leadership after Olivia approves it.",
    trigger: { schedule: "0 8 * * MON" },
    steps: [
      {
        id: "gather",
        research: "Summarize this week's operations: safety, shipping lead times, returns, incidents.",
      },
      {
        id: "send",
        tool: "send_email",
        arguments: {
          to: ["leadership@acme.test"],
          subject: "Weekly operations report {{ today }}",
          body: "{{ steps.gather.text }}",
        },
        approver: "olivia.grant@acme.test",
      },
    ],
  },
} as const;

export const BLANK_DEFINITION = {
  trigger: {},
  steps: [{ id: "gather", research: "What should this workflow find out?" }],
};

export function definitionText(definition: unknown): string {
  return JSON.stringify(definition, null, 2);
}

export type ParsedDefinition =
  | { ok: true; definition: Record<string, unknown> }
  | { ok: false; message: string; line: number | null; column: number | null };

/** "Unexpected token } in JSON at position 41" → line and column, for the editor. */
export function lineColumn(text: string, position: number): { line: number; column: number } {
  const before = text.slice(0, Math.max(0, position));
  const line = before.split("\n").length;
  const column = position - before.lastIndexOf("\n");
  return { line, column };
}

export function parseDefinition(text: string): ParsedDefinition {
  if (!text.trim()) return { ok: false, message: "The definition is empty.", line: null, column: null };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Not valid JSON.";
    const at = /position (\d+)/.exec(message);
    const lc = /line (\d+) column (\d+)/.exec(message);
    if (lc) return { ok: false, message: `Not valid JSON: ${message}`, line: Number(lc[1]), column: Number(lc[2]) };
    if (at) {
      const { line, column } = lineColumn(text, Number(at[1]));
      return { ok: false, message: `Not valid JSON: ${message}`, line, column };
    }
    return { ok: false, message: `Not valid JSON: ${message}`, line: null, column: null };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { ok: false, message: "The definition must be a JSON object with `steps`.", line: null, column: null };
  }
  const steps = (parsed as { steps?: unknown }).steps;
  if (!Array.isArray(steps) || steps.length === 0) {
    return { ok: false, message: "Add at least one step to `steps`.", line: null, column: null };
  }
  return { ok: true, definition: parsed as Record<string, unknown> };
}

export interface DefinitionProblem {
  /** "Step 2 › tool", or "" for the whole definition. */
  where: string;
  message: string;
}

/** 422 invalid_workflow → readable problems, naming the step by its id when we know it. */
export function workflowProblems(error: unknown, definition: Record<string, unknown> | null): DefinitionProblem[] {
  const errors: DetailError[] = detailErrors(error, ["invalid_workflow"]);
  const steps = Array.isArray(definition?.steps) ? (definition.steps as { id?: unknown }[]) : [];
  return errors.map((e) => {
    const match = /^steps\.(\d+)(?:\.(.+))?$/.exec(e.path);
    if (match) {
      const step = steps[Number(match[1])];
      const stepId = typeof step?.id === "string" && step.id ? step.id : null;
      const name = stepId ? `Step “${stepId}”` : `Step ${Number(match[1]) + 1}`;
      return { where: match[2] ? `${name} › ${match[2]}` : name, message: withoutStepPrefix(e.message, stepId) };
    }
    return { where: describePath(e.path), message: e.message };
  });
}

/** "Step send: give either …" under "Step “send”" → "Give either …". */
function withoutStepPrefix(message: string, stepId: string | null): string {
  if (!stepId) return message;
  const escaped = stepId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rest = message.replace(new RegExp(`^step ${escaped}:\\s*`, "i"), "");
  return rest === message ? message : rest.charAt(0).toUpperCase() + rest.slice(1);
}

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  waiting_approval: "Waiting for approval",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const STEP_STATUS_LABELS: Record<string, string> = {
  done: "Done",
  failed: "Failed",
  waiting_approval: "Waiting for approval",
  rejected: "Rejected",
  expired: "Expired",
  running: "Running",
};

export function isRunActive(run: Pick<WorkflowRun, "status">): boolean {
  return run.status === "queued" || run.status === "running" || run.status === "waiting_approval";
}

/** Who or what started a run, in words. */
export function runTriggerText(trigger: string): string {
  if (trigger === "manual") return "Run now";
  if (trigger === "schedule") return "Schedule";
  if (trigger === "event") return "A document became ready";
  return trigger;
}

/**
 * Where a workflow run lives in the app: Admin › Workflows with its runs open (notifications and
 * approvals link here; the API builds the same link in modules/work/links.py).
 */
export function workflowRunHref(key: string, runId?: string | null): `/admin/workflows?${string}` {
  const params = new URLSearchParams({ workflow: key });
  if (runId) params.set("run", runId);
  return `/admin/workflows?${params.toString()}`;
}

/** The workflow (and run) a link asks to open, once the list is loaded; null if none or gone. */
export function linkedWorkflow(
  workflows: readonly Workflow[],
  params: { get: (name: string) => string | null },
): { workflow: Workflow | null; key: string; runId: string | null } | null {
  const key = params.get("workflow");
  if (!key) return null;
  return { workflow: workflows.find((w) => w.key === key) ?? null, key, runId: params.get("run") };
}

export interface RunStepView {
  id: string;
  kind: "research" | "tool" | "unknown";
  /** What the step does: the research question, or the tool name. */
  what: string;
  status: string;
  output: StepOutput | null;
}

/**
 * A run's steps in the definition's order (PostgreSQL JSONB doesn't keep key order); steps
 * the definition no longer has come last. Steps not reached yet are "pending".
 */
export function runSteps(run: WorkflowRun, definition: WorkflowDefinition | null): RunStepView[] {
  const defined = definition?.steps ?? [];
  const views: RunStepView[] = defined.map((step) => {
    const output = run.steps[step.id] ?? null;
    const status = output?.status ?? (run.current_step === step.id && run.status === "running" ? "running" : "pending");
    return {
      id: step.id,
      kind: step.research != null ? "research" : step.tool != null ? "tool" : "unknown",
      what: step.research ?? step.tool ?? "",
      status,
      output,
    };
  });
  for (const [stepId, output] of Object.entries(run.steps)) {
    if (defined.some((s) => s.id === stepId)) continue;
    views.push({ id: stepId, kind: "unknown", what: "", status: output?.status ?? "done", output });
  }
  return views;
}

/** "Every Monday 08:00 UTC"-ish summary of the trigger; cron stays visible for precision. */
export function triggerText(definition: WorkflowDefinition): string {
  const t = definition.trigger ?? {};
  if (t.schedule) return `Schedule: ${t.schedule} (UTC)`;
  if (t.event) return t.collection ? `When a document is ready in ${t.collection}` : "When a document is ready";
  return "Manual only";
}

/** The approval a run waits for, if any. */
export function waitingApprovalId(run: WorkflowRun): string | null {
  if (run.status !== "waiting_approval") return null;
  const current = run.current_step ? run.steps[run.current_step] : null;
  if (current?.approval_id) return current.approval_id;
  return Object.values(run.steps).find((s) => s?.status === "waiting_approval" && s.approval_id)?.approval_id ?? null;
}
