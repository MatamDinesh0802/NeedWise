import { describe, expect, it } from "vitest";

import { answerFromRun } from "./answer";
import { toolActivity, toolStepLabel } from "./research";
import { initialRunState, reduceRunEvent, type RunEvent, type RunState } from "./run-reducer";

/** A research run as runner.py emits it: tools, one proposed email, then the text in pieces. */
const RESEARCH: RunEvent[] = [
  { id: "1730000000000-0", type: "run.started", data: { run_id: "r1", conversation_id: "c1" } },
  { id: "1730000001000-0", type: "tool.step", data: { tool: "search_knowledge", ok: true, error: null } },
  { id: "1730000002000-0", type: "tool.step", data: { tool: "search_knowledge", ok: true, error: null } },
  { id: "1730000003000-0", type: "tool.step", data: { tool: "web_search", ok: false, error: "upstream_unavailable" } },
  { id: "1730000004000-0", type: "tool.step", data: { tool: "send_email", ok: true, error: null } },
  {
    id: "1730000004000-1",
    type: "approval.required",
    data: { approval_id: "ap1", tool: "send_email", summary: "Send an email to leadership@acme.test: Weekly Operations Report" },
  },
  { id: "1730000005000-0", type: "token", data: { text: "I drafted this week's report. It waits for your approval" } },
  { id: "1730000005000-1", type: "token", data: { text: " before anything is sent." } },
  { id: "1730000005000-2", type: "run.completed", data: { message_id: "m2", usage: { model: "claude-opus-5-5" }, approvals: ["ap1"] } },
];

const play = (events: RunEvent[], from: RunState = initialRunState("r1")) => events.reduce(reduceRunEvent, from);

describe("research runs in the reducer", () => {
  it("collects tool steps in order and the proposed approval", () => {
    const state = play(RESEARCH);
    expect(state.phase).toBe("completed");
    expect(state.toolSteps).toEqual([
      { tool: "search_knowledge", ok: true, error: null },
      { tool: "search_knowledge", ok: true, error: null },
      { tool: "web_search", ok: false, error: "upstream_unavailable" },
      { tool: "send_email", ok: true, error: null },
    ]);
    expect(state.approvals).toEqual([
      { id: "ap1", tool: "send_email", summary: "Send an email to leadership@acme.test: Weekly Operations Report" },
    ]);
    expect(state.text).toBe("I drafted this week's report. It waits for your approval before anything is sent.");
  });

  it("stays in 'searching' while tools run, then streams the text", () => {
    expect(play(RESEARCH.slice(0, 5)).phase).toBe("searching");
    expect(play(RESEARCH.slice(0, 7)).phase).toBe("streaming");
  });

  it("ignores replayed events after a reconnect: no doubled steps, approvals or text", () => {
    const halfway = play(RESEARCH.slice(0, 6));
    const replayed = play([...RESEARCH.slice(0, 6), ...RESEARCH.slice(6)], halfway); // the browser resends from the start
    expect(replayed).toEqual(play(RESEARCH));
    expect(replayed.toolSteps).toHaveLength(4);
    expect(replayed.approvals).toHaveLength(1);
  });

  it("never lists the same approval twice, even with a new event id", () => {
    const state = play([
      ...RESEARCH.slice(0, 6),
      { id: "1730000004500-0", type: "approval.required", data: { approval_id: "ap1", tool: "send_email", summary: "again" } },
    ]);
    expect(state.approvals).toHaveLength(1);
  });

  it("adds approvals only named in run.completed (e.g. an event that was missed)", () => {
    const state = play([
      RESEARCH[0],
      { id: "1730000009000-0", type: "run.completed", data: { message_id: "m2", usage: {}, approvals: ["ap1", "ap2"] } },
    ]);
    expect(state.approvals).toEqual([
      { id: "ap1", tool: "", summary: "" },
      { id: "ap2", tool: "", summary: "" },
    ]);
  });

  it("drops malformed research events", () => {
    const state = play([RESEARCH[0], { id: "1730000001000-0", type: "tool.step", data: { ok: true } }, { id: "1730000002000-0", type: "approval.required", data: {} }]);
    expect(state.toolSteps).toEqual([]);
    expect(state.approvals).toEqual([]);
  });

  it("passes tool steps and approvals to the answer view", () => {
    const answer = answerFromRun(play(RESEARCH));
    expect(answer.toolSteps).toHaveLength(4);
    expect(answer.approvals.map((a) => a.id)).toEqual(["ap1"]);
  });

  it("reads agent display names from plan and agent.step", () => {
    const state = play([
      RESEARCH[0],
      { id: "1730000001000-0", type: "plan", data: { mode: "fan_out", routed_by: "planner", subtasks: [{ agent: "support", name: "Customer Support", goal: "P1" }] } },
      { id: "1730000002000-0", type: "agent.step", data: { agent: "ops", name: "Operations", status: "running", label: "Distance" } },
    ]);
    expect(state.agents.map((a) => [a.agent, a.name])).toEqual([
      ["support", "Customer Support"],
      ["ops", "Operations"],
    ]);
    expect(state.plan?.subtasks).toEqual([{ agent: "support", goal: "P1" }]);
  });
});

describe("tool activity labels", () => {
  it.each([
    [{ tool: "search_knowledge", ok: true, error: null }, "Searched company documents"],
    [{ tool: "send_email", ok: true, error: null }, "Prepared an email for your approval"],
    [{ tool: "create_task", ok: true, error: null }, "Prepared a task for your approval"],
    // What the API actually sends for a proposal: not run, waiting for approval.
    [{ tool: "send_email", ok: false, error: "approval_required" }, "Prepared an email for your approval"],
    [{ tool: "erp.send_report", ok: false, error: "approval_required" }, "Prepared erp send report for your approval"],
    [{ tool: "web_search", ok: false, error: "upstream_unavailable" }, "Couldn't search the web (service unavailable)"],
    [{ tool: "erp.stock_level", ok: true, error: null }, "Used erp stock level"],
    [{ tool: "erp.stock_level", ok: false, error: "rate_limited" }, "Erp stock level failed (rate limited)"],
    [
      { tool: "search_knowledge", ok: false, error: "limit_reached" },
      "Couldn't search company documents (skipped: the run's tool-call limit was reached)",
    ],
  ])("%j → %s", (step, label) => {
    expect(toolStepLabel(step)).toBe(label);
  });

  it("shows a proposal as done, not failed", () => {
    expect(toolActivity([{ tool: "send_email", ok: false, error: "approval_required" }])).toEqual([
      { key: "0-send_email", label: "Prepared an email for your approval", ok: true, count: 1 },
    ]);
  });

  it("folds repeats into one line with a count", () => {
    const steps = play(RESEARCH).toolSteps;
    expect(toolActivity(steps).map((l) => [l.label, l.count, l.ok])).toEqual([
      ["Searched company documents", 2, true],
      ["Couldn't search the web (service unavailable)", 1, false],
      ["Prepared an email for your approval", 1, true],
    ]);
  });
});
