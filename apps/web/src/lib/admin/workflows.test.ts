import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import {
  definitionText,
  isRunActive,
  linkedWorkflow,
  parseDefinition,
  runSteps,
  runTriggerText,
  triggerText,
  waitingApprovalId,
  WEEKLY_OPS_TEMPLATE,
  workflowProblems,
  workflowRunHref,
  type WorkflowDefinition,
  type WorkflowRun,
} from "./workflows";

const definition = WEEKLY_OPS_TEMPLATE.definition as unknown as WorkflowDefinition;

describe("definition editor", () => {
  it("round-trips the weekly-ops template", () => {
    const parsed = parseDefinition(definitionText(WEEKLY_OPS_TEMPLATE.definition));
    expect(parsed).toEqual({ ok: true, definition: WEEKLY_OPS_TEMPLATE.definition });
  });

  it("points at the line and column of a JSON mistake", () => {
    const text = '{\n  "steps": [\n    { "id": "gather", }\n  ]\n}';
    const parsed = parseDefinition(text);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) {
      expect(parsed.message).toMatch(/^Not valid JSON/);
      expect(parsed.line).toBe(3);
      expect(parsed.column).toBeGreaterThan(1);
    }
  });

  it("asks for an object with at least one step", () => {
    expect(parseDefinition("")).toMatchObject({ ok: false, message: "The definition is empty." });
    expect(parseDefinition("[]")).toMatchObject({ ok: false, message: "The definition must be a JSON object with `steps`." });
    expect(parseDefinition('{"steps": []}')).toMatchObject({ ok: false, message: "Add at least one step to `steps`." });
  });
});

describe("422 invalid_workflow display", () => {
  it("names the failing step by its id", () => {
    const detail = `2 validation errors for Definition
steps.1
  Value error, step send: give either \`research\` or \`tool\` [type=value_error, input_value={'id': 'send'}, input_type=dict]
trigger.schedule
  Value error, invalid cron expression '0 8 * MON' [type=value_error, input_value='0 8 * MON', input_type=str]`;
    const error = new ApiError({ status: 422, title: "The workflow definition is invalid", code: "invalid_workflow", detail });
    expect(workflowProblems(error, WEEKLY_OPS_TEMPLATE.definition)).toEqual([
      { where: "Step “send”", message: "Give either `research` or `tool`" },
      { where: "trigger › schedule", message: "Invalid cron expression '0 8 * MON'" },
    ]);
  });

  it("names a field inside a step, and ignores other errors", () => {
    const detail = "1 validation error for Definition\nsteps.0.id\n  String should match pattern '^[a-z0-9]+(-[a-z0-9]+)*$' [type=string_pattern_mismatch]";
    const error = new ApiError({ status: 422, title: "Invalid", code: "invalid_workflow", detail });
    expect(workflowProblems(error, { steps: [{ id: "Gather" }] })).toEqual([{ where: "Step “Gather” › id", message: "Not in the expected format." }]);
    expect(workflowProblems(new ApiError({ status: 409, title: "x", code: "slug_taken" }), null)).toEqual([]);
  });
});

describe("run history", () => {
  const run = (overrides: Partial<WorkflowRun>): WorkflowRun => ({
    id: "run1",
    status: "waiting_approval",
    trigger: "manual",
    // JSONB returns keys in its own order: "send" before "gather".
    steps: { send: { status: "waiting_approval", approval_id: "ap9" }, gather: { status: "done", text: "Safety: no incidents." } },
    current_step: "send",
    error: null,
    created_at: "2026-11-04T08:00:00Z",
    finished_at: null,
    ...overrides,
  });

  it("shows steps in the definition's order, with outputs", () => {
    expect(runSteps(run({}), definition).map((s) => [s.id, s.kind, s.status])).toEqual([
      ["gather", "research", "done"],
      ["send", "tool", "waiting_approval"],
    ]);
    expect(runSteps(run({}), definition)[0].output?.text).toBe("Safety: no incidents.");
  });

  it("marks steps not reached yet as pending and the current one as running", () => {
    expect(runSteps(run({ status: "running", steps: {}, current_step: "gather" }), definition).map((s) => s.status)).toEqual(["running", "pending"]);
  });

  it("keeps outputs of steps the definition no longer has", () => {
    const views = runSteps(run({ steps: { old: { status: "done", text: "x" } }, status: "succeeded" }), definition);
    expect(views.map((s) => s.id)).toEqual(["gather", "send", "old"]);
  });

  it("finds the approval a run waits for", () => {
    expect(waitingApprovalId(run({}))).toBe("ap9");
    expect(waitingApprovalId(run({ status: "succeeded" }))).toBeNull();
  });

  it("describes triggers", () => {
    expect(triggerText(definition)).toBe("Schedule: 0 8 * * MON (UTC)");
    expect(triggerText({ steps: [], trigger: { event: "document.ready", collection: "finance" } })).toBe("When a document is ready in finance");
    expect(triggerText({ steps: [] })).toBe("Manual only");
  });
});

describe("links to a workflow run", () => {
  const workflow = { id: "w1", key: "weekly-ops-report", name: "Weekly", definition, enabled: true, owner_id: "u1" };

  it("builds the same link as the API's notifications (modules/work/links.py)", () => {
    expect(workflowRunHref("weekly-ops-report", "r1")).toBe("/admin/workflows?workflow=weekly-ops-report&run=r1");
    expect(workflowRunHref("weekly-ops-report")).toBe("/admin/workflows?workflow=weekly-ops-report");
  });

  it("opens the linked workflow and run, or says it is gone", () => {
    const params = (query: string) => new URLSearchParams(query);
    expect(linkedWorkflow([workflow], params("workflow=weekly-ops-report&run=r1"))).toEqual({
      workflow,
      key: "weekly-ops-report",
      runId: "r1",
    });
    expect(linkedWorkflow([workflow], params("workflow=deleted"))).toEqual({ workflow: null, key: "deleted", runId: null });
    expect(linkedWorkflow([workflow], params(""))).toBeNull();
  });

  it("counts a queued (event) run as active, and says what started a run", () => {
    expect(isRunActive({ status: "queued" })).toBe(true);
    expect(isRunActive({ status: "cancelled" })).toBe(false);
    expect(runTriggerText("event")).toBe("A document became ready");
    expect(runTriggerText("manual")).toBe("Run now");
    expect(runTriggerText("schedule")).toBe("Schedule");
  });
});
