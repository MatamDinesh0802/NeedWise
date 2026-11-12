import { describe, expect, it } from "vitest";

import type { Approval } from "@/lib/work/api";

import { answerFromMessage, approvalsByRunId, researchFromMessage } from "./answer";
import type { Message } from "./api";

const message = (overrides: Partial<Message>): Message => ({
  id: "m",
  role: "user",
  content: "",
  status: "complete",
  created_at: "2026-11-12T09:00:00Z",
  run_id: null,
  search_query: null,
  citations: [],
  sources: [],
  usage: {},
  abstained: false,
  withheld: 0,
  feedback: null,
  feedback_comment: null,
  ...overrides,
});

const researched = message({
  id: "a1",
  role: "assistant",
  content: "The report is drafted and waits for your approval.",
  run_id: "r1",
  tool_steps: [
    { tool: "search_knowledge", ok: true, error: null, approval_id: null, summary: null },
    { tool: "search_knowledge", ok: true, error: null, approval_id: null, summary: null },
    { tool: "web_search", ok: false, error: "not_configured", approval_id: null, summary: null },
    {
      tool: "send_email",
      ok: false,
      error: "approval_required",
      approval_id: "ap1",
      summary: "Send an email to leadership@acme.test: Weekly report",
    },
  ],
});

const approval = (id: string, origin: Approval["origin"]): Approval => ({
  id,
  tool: "send_email",
  summary: "Send an email",
  arguments: {},
  edited_arguments: null,
  status: "pending",
  requested_by: "u1",
  approver_id: "u1",
  decided_at: null,
  decision_note: null,
  expires_at: "2999-01-01T00:00:00Z",
  result: null,
  origin,
  created_at: "2026-11-12T09:00:00Z",
});

describe("researchFromMessage", () => {
  it("rebuilds a reloaded research answer's steps and the approvals it proposed", () => {
    expect(researchFromMessage(researched)).toEqual({
      toolSteps: [
        { tool: "search_knowledge", ok: true, error: null },
        { tool: "search_knowledge", ok: true, error: null },
        { tool: "web_search", ok: false, error: "not_configured" },
        { tool: "send_email", ok: false, error: "approval_required" },
      ],
      approvals: [{ id: "ap1", tool: "send_email", summary: "Send an email to leadership@acme.test: Weekly report" }],
    });
  });

  it("is empty for plain answers and for messages from an older API", () => {
    expect(researchFromMessage(message({ role: "assistant", tool_steps: [] }))).toBeNull();
    expect(researchFromMessage(message({ role: "assistant" }))).toBeNull();
  });
});

describe("answerFromMessage", () => {
  it("shows the stored steps after a reload, as the live run did", () => {
    const view = answerFromMessage(researched, "Email the weekly report");
    expect(view.toolSteps).toHaveLength(4);
    expect(view.approvals.map((a) => a.id)).toEqual(["ap1"]);
  });

  it("prefers this tab's memory of the live run when it has one", () => {
    const live = { toolSteps: [{ tool: "draft_email", ok: true, error: null }], approvals: [] };
    const view = answerFromMessage(researched, "Email the weekly report", null, live);
    expect(view.toolSteps).toEqual(live.toolSteps);
    expect(view.approvals).toEqual([]);
  });
});

describe("approvalsByRunId", () => {
  it("groups a conversation's approvals under the answer (run) that proposed them", () => {
    const grouped = approvalsByRunId(
      [
        approval("ap1", { conversation_id: "c1", run_id: "r1" }),
        approval("ap2", { conversation_id: "c1", run_id: "r1" }),
        approval("ap3", { conversation_id: "c1", run_id: "r2" }),
        approval("ap4", { conversation_id: "c2", run_id: "r9" }),
        approval("ap5", { via: "workflow", workflow_run_id: "w1" }),
      ],
      "c1",
    );
    expect([...grouped.entries()].map(([run, list]) => [run, list.map((a) => a.id)])).toEqual([
      ["r1", ["ap1", "ap2"]],
      ["r2", ["ap3"]],
    ]);
  });
});
