import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import {
  describeSourcePath,
  isRunActive,
  lineRange,
  linkedWorkflow,
  problemView,
  runSteps,
  runTriggerText,
  sourceProblems,
  triggerText,
  waitingApprovalId,
  WEEKLY_OPS_TEMPLATE,
  workflowRunHref,
  type WorkflowDefinition,
  type WorkflowRun,
} from "./workflows";

const definition: WorkflowDefinition = {
  trigger: { schedule: "0 8 * * MON" },
  steps: [
    { id: "gather", research: "Summarize this week's operations." },
    { id: "send", tool: "send_email", arguments: {}, approver: "olivia.grant@acme.test" },
  ],
};

describe("definition editor", () => {
  it("offers the weekly-ops example as commented YAML", () => {
    expect(WEEKLY_OPS_TEMPLATE.source).toContain('schedule: "0 8 * * MON"');
    expect(WEEKLY_OPS_TEMPLATE.source).toContain("approver: olivia.grant@acme.test");
    expect(WEEKLY_OPS_TEMPLATE.source).toContain("# Olivia approves");
  });

  it("selects a whole line to jump to a problem", () => {
    const text = "steps:\n  - id: a\n    research: b";
    expect(lineRange(text, 1)).toEqual({ start: 0, end: 6 });
    expect(text.slice(lineRange(text, 2).start, lineRange(text, 2).end)).toBe("  - id: a");
    expect(lineRange(text, 3).end).toBe(text.length);
    expect(lineRange(text, 9)).toEqual({ start: text.length, end: text.length });
  });

  it("lists a problem under the step's id, without repeating it", () => {
    const problem = { line: 7, column: 5, path: "steps[1]", message: "step send: give either `research` or `tool`" };
    expect(problemView(problem)).toEqual({ line: 7, where: "Step “send”", message: "Give either `research` or `tool`" });
    expect(problemView({ ...problem, path: "steps[1].approver", message: "approver: role must be one of org_admins" }, definition.steps)).toEqual({
      line: 7,
      where: "Step “send” › approver",
      message: "approver: role must be one of org_admins",
    });
    expect(problemView({ line: null, column: null, path: "(definition)", message: "Not valid YAML: x" })).toEqual({ line: null, where: "", message: "Not valid YAML: x" });
  });

  it("names steps by their id", () => {
    expect(describeSourcePath("steps[1].tool", definition.steps)).toBe("Step “send” › tool");
    expect(describeSourcePath("steps[4]", definition.steps)).toBe("Step 5");
    expect(describeSourcePath("trigger.schedule")).toBe("trigger › schedule");
    expect(describeSourcePath("(definition)")).toBe("");
  });
});

describe("422 invalid_workflow display", () => {
  it("lists the API's problems at their lines", () => {
    const errors = [{ line: 7, column: 5, path: "steps[1]", message: "step send: give either `research` or `tool`" }];
    const error = new ApiError({
      status: 422,
      title: "The workflow definition is invalid",
      code: "invalid_workflow",
      detail: "line 7: steps[1]: step send: give either `research` or `tool`",
      problem: { type: "about:blank", title: "x", status: 422, code: "invalid_workflow", errors },
    });
    expect(sourceProblems(error)).toEqual(errors);
  });

  it("falls back to the detail, and ignores other errors", () => {
    const error = new ApiError({ status: 422, title: "Invalid", code: "invalid_workflow", detail: "Give the definition or its source, not both" });
    expect(sourceProblems(error)).toEqual([{ line: null, column: null, path: "(definition)", message: "Give the definition or its source, not both" }]);
    expect(sourceProblems(new ApiError({ status: 409, title: "x", code: "slug_taken" }))).toEqual([]);
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
    expect(triggerText({ steps: [], trigger: { event: "task.completed", department: "finance" } })).toBe(
      "When a task is completed in the finance queue",
    );
    expect(triggerText({ steps: [], trigger: { event: "approval.decided", tool: "send_email" } })).toBe("When an approval is decided (send_email)");
    expect(triggerText({ steps: [], trigger: { event: "meeting.processed" } })).toBe("When a meeting's notes are ready");
    expect(triggerText({ steps: [] })).toBe("Manual only");
  });
});

describe("links to a workflow run", () => {
  const workflow = { id: "w1", key: "weekly-ops-report", name: "Weekly", definition, source: "", enabled: true, owner_id: "u1" };

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
    expect(runTriggerText("event")).toBe("An event");
    expect(runTriggerText("manual")).toBe("Run now");
    expect(runTriggerText("schedule")).toBe("Schedule");
  });
});
