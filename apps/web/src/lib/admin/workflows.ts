/**
 * Admin → Workflows. Shapes mirror apps/api/src/needwise/api/v1/work.py (admin_router) and
 * modules/work/workflows.py (Definition, Step). The editor takes the definition as YAML (or JSON,
 * which is YAML too); the API checks it (POST /admin/workflows/validate while typing, and on
 * saving) and reports each problem at its line (modules/work/workflow_source.py).
 */

import { ApiError, apiJson } from "@/lib/api";

export interface WorkflowStep {
  id: string;
  research?: string;
  tool?: string;
  arguments?: Record<string, unknown>;
  /** An email, or an approver rule ({role: org_admins}, {chain: [...]}, ...). */
  approver?: unknown;
}

export interface WorkflowDefinition {
  description?: string;
  trigger?: {
    schedule?: string | null;
    event?: string | null;
    collection?: string | null;
    department?: string | null;
    tool?: string | null;
  };
  steps: WorkflowStep[];
}

export interface Workflow {
  id: string;
  key: string;
  name: string;
  definition: WorkflowDefinition;
  /** The definition as written (comments kept), or as YAML when it was saved as an object. */
  source: string;
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
  /** YAML or JSON text. */
  source: string;
  enabled: boolean;
}

/** One problem in the definition, at its line and column (1-based) when it has one. */
export interface SourceProblem {
  line: number | null;
  column: number | null;
  /** Where in the definition, e.g. "steps[1].tool"; "(definition)" for the whole text. */
  path: string;
  message: string;
}

export interface SourceCheck {
  valid: boolean;
  errors: SourceProblem[];
  definition: WorkflowDefinition | null;
}

const id = (value: string) => encodeURIComponent(value);

export const workflowsApi = {
  list: () => apiJson<Workflow[]>("/api/v1/admin/workflows"),
  save: (body: WorkflowSave) => apiJson<Workflow>(`/api/v1/admin/workflows/${id(body.key)}`, { method: "PUT", json: body }),
  /** Checks a definition without saving it (people, departments and collections it names are checked on saving). */
  validate: (source: string) => apiJson<SourceCheck>("/api/v1/admin/workflows/validate", { method: "POST", json: { source } }),
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

/** The example from docs/automation.md: research, then an email that waits for two approvers. */
export const WEEKLY_OPS_TEMPLATE = {
  key: "weekly-ops-report",
  name: "Weekly operations report",
  source: `description: Every Monday, last week's operations, emailed to leadership once approved.
trigger:
  schedule: "0 8 * * MON"        # cron, UTC; leave the trigger out for manual only
steps:
  - id: gather
    research: >-
      Summarize this week's operations: safety, shipping lead times, returns, incidents.
  - id: send
    tool: send_email
    arguments:
      to: [leadership@acme.test]
      subject: "Weekly operations report {{ today }}"
      body: "{{ steps.gather.text }}"
    # Olivia approves; or a rule, e.g. {role: org_admins} or
    # {chain: [{department_manager: requester}, priya.raman@acme.test]}
    approver: olivia.grant@acme.test
`,
} as const;

export const BLANK_SOURCE = `# Leave the trigger out to run it only by hand.
trigger:
  schedule: "0 8 * * MON"
steps:
  - id: gather
    research: What should this workflow find out?
`;

/** 422 invalid_workflow → the problems at their lines (the API's `errors`); [] for other errors. */
export function sourceProblems(error: unknown): SourceProblem[] {
  if (!(error instanceof ApiError) || error.code !== "invalid_workflow") return [];
  const listed = error.problem?.errors;
  if (Array.isArray(listed)) {
    return listed.filter(isProblem);
  }
  return [{ line: null, column: null, path: "(definition)", message: error.detail ?? error.title }];
}

function isProblem(value: unknown): value is SourceProblem {
  return typeof value === "object" && value !== null && typeof (value as SourceProblem).message === "string" && typeof (value as SourceProblem).path === "string";
}

/** "steps[1].tool" → "Step “send” › tool" (the step's id when the definition has it). */
export function describeSourcePath(path: string, steps: readonly { id?: unknown }[] = []): string {
  if (path === "(definition)") return "";
  const match = /^steps\[(\d+)\](?:\.(.+))?$/.exec(path);
  if (match) {
    const step = steps[Number(match[1])];
    const name = typeof step?.id === "string" && step.id ? `Step “${step.id}”` : `Step ${Number(match[1]) + 1}`;
    return match[2] ? `${name} › ${match[2].replace(/\./g, " › ")}` : name;
  }
  return path.replace(/\./g, " › ");
}

/**
 * A problem as the editor lists it: "Step “send”" and "Give either …", not "Step 2" and "step
 * send: give either …" (the API names the step in the message when the definition is invalid).
 */
export function problemView(problem: SourceProblem, steps: readonly { id?: unknown }[] = []): { line: number | null; where: string; message: string } {
  let where = describeSourcePath(problem.path, steps);
  let message = problem.message;
  const named = /^step ([a-z0-9-]+):\s*([\s\S]+)$/i.exec(message);
  if (named && /^steps\[\d+\]/.test(problem.path)) {
    where = where.replace(/^Step (?:\d+|“[^”]*”)/, `Step “${named[1]}”`);
    message = named[2].charAt(0).toUpperCase() + named[2].slice(1);
  }
  return { line: problem.line, where, message };
}

/** The character range of a 1-based line, to select it in the editor. */
export function lineRange(text: string, line: number): { start: number; end: number } {
  let start = 0;
  for (let current = 1; current < line; current += 1) {
    const next = text.indexOf("\n", start);
    if (next === -1) return { start: text.length, end: text.length };
    start = next + 1;
  }
  const end = text.indexOf("\n", start);
  return { start, end: end === -1 ? text.length : end };
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
  if (trigger === "event") return "An event";
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

/** Each event a workflow can wait for, in words (modules/work/events.py). */
export const EVENT_TEXT: Record<string, string> = {
  "document.ready": "When a document is ready",
  "document.updated": "When a document gets a new version",
  "meeting.processed": "When a meeting's notes are ready",
  "task.completed": "When a task is completed",
  "approval.decided": "When an approval is decided",
};

/** "Every Monday 08:00 UTC"-ish summary of the trigger; cron stays visible for precision. */
export function triggerText(definition: WorkflowDefinition): string {
  const t = definition.trigger ?? {};
  if (t.schedule) return `Schedule: ${t.schedule} (UTC)`;
  if (t.event) {
    const base = EVENT_TEXT[t.event] ?? `On ${t.event}`;
    if (t.collection) return `${base} in ${t.collection}`;
    if (t.department) return `${base} in the ${t.department} queue`;
    if (t.tool) return `${base} (${t.tool})`;
    return base;
  }
  return "Manual only";
}

/** The approval a run waits for, if any. */
export function waitingApprovalId(run: WorkflowRun): string | null {
  if (run.status !== "waiting_approval") return null;
  const current = run.current_step ? run.steps[run.current_step] : null;
  if (current?.approval_id) return current.approval_id;
  return Object.values(run.steps).find((s) => s?.status === "waiting_approval" && s.approval_id)?.approval_id ?? null;
}
