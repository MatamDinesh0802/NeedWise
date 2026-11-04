import { describe, expect, it } from "vitest";

import { initialRunState, type AgentStep, type RunState } from "./run-reducer";
import {
  agentName,
  agentsWorking,
  failureText,
  formatSeconds,
  planSentence,
  routedByLabel,
  stepLine,
  traceFromRun,
  traceSummary,
} from "./trace";

const step = (overrides: Partial<AgentStep>): AgentStep => ({
  agent: "finance",
  goal: "hotel limit in London",
  status: "done",
  label: "hotel limit in London",
  findings: 6,
  sources: 8,
  seconds: 8.1,
  ...overrides,
});

const run = (overrides: Partial<RunState>): RunState => ({ ...initialRunState("r1"), ...overrides });

describe("traceFromRun", () => {
  it("has no trace without agents (direct, or single plans answered by the plain pipeline)", () => {
    expect(traceFromRun(run({}))).toBeNull();
    expect(traceFromRun(run({ plan: { mode: "single", routedBy: "fast_path", subtasks: [] }, agents: [] }))).toBeNull();
  });

  it("has no trace for a single plan whose only entries were seeded from the plan (auto mode)", () => {
    const seeded = step({ status: "pending", findings: 0, seconds: null });
    expect(traceFromRun(run({ plan: { mode: "single", routedBy: "fast_path", subtasks: [{ agent: "engineering", goal: "x" }] }, agents: [seeded] }))).toBeNull();
  });

  it("has a trace for fan-out plans and whenever agent steps arrived", () => {
    expect(traceFromRun(run({ plan: { mode: "fan_out", routedBy: "planner", subtasks: [] } }))).not.toBeNull();
    expect(traceFromRun(run({ agents: [step({})] }))?.steps).toHaveLength(1);
  });

  it("measures the run on the server clock from the stream ids", () => {
    expect(traceFromRun(run({ agents: [step({})], startedAtMs: 1_000, endedAtMs: 20_400 }))?.totalSeconds).toBe(19.4);
    expect(traceFromRun(run({ agents: [step({})], startedAtMs: 1_000, endedAtMs: null }))?.totalSeconds).toBeNull();
  });
});

describe("formatting", () => {
  it.each([
    ["finance", "Finance agent"],
    ["it-ops", "It ops agent"],
    ["customer_support", "Customer support agent"],
    ["safety-agent", "Safety agent"],
    ["", "Agent"],
  ])("agentName(%j) → %s", (key, name) => {
    expect(agentName(key)).toBe(name);
  });

  it.each([
    [0.4, "0.4 s"],
    [8.14, "8.1 s"],
    [19.4, "19 s"],
    [60, "1 min"],
    [75.2, "1 min 15 s"],
  ])("formatSeconds(%f) → %s", (s, text) => {
    expect(formatSeconds(s)).toBe(text);
  });

  it("describes the plan in one sentence", () => {
    const subtasks = [{ agent: "a", goal: "x" }, { agent: "b", goal: "y" }];
    expect(planSentence({ mode: "fan_out", routedBy: "planner", subtasks })).toBe("The planner split the question into 2 parts.");
    expect(planSentence({ mode: "single", routedBy: "fast_path", subtasks: subtasks.slice(0, 1) })).toMatch(/^Matched instantly.*1 part/);
    expect(planSentence({ mode: "fan_out", routedBy: "fallback", subtasks })).toMatch(/fallback plan/);
  });

  it("explains how the plan was made", () => {
    expect(routedByLabel("fast_path")).toBe("matched instantly");
    expect(routedByLabel("planner")).toBe("planned");
    expect(routedByLabel("fallback")).toMatch(/fallback/);
  });

  it("writes one calm line per agent", () => {
    expect(stepLine(step({}))).toEqual({ name: "Finance agent", goal: "hotel limit in London", status: "6 findings · 8.1 s" });
    expect(stepLine(step({ findings: 1, seconds: null })).status).toBe("1 finding");
    expect(stepLine(step({ status: "running" })).status).toBe("searching…");
    expect(stepLine(step({ status: "pending" })).status).toBe("waiting");
    expect(stepLine(step({ status: "failed", label: "timed out after 90 s" })).status).toBe("timed out; answered without it");
    expect(failureText(step({ status: "failed", label: "Connection refused" }))).toBe("unavailable; answered without it");
  });

  it("summarizes the trace in one line", () => {
    const three = { plan: null, steps: [step({}), step({ agent: "support" }), step({ agent: "safety" })], totalSeconds: 19.4 };
    expect(traceSummary(three)).toBe("Answered by 3 agents · 19 s");
    expect(traceSummary({ ...three, steps: [step({}), step({ agent: "safety", status: "failed" })] })).toBe(
      "Answered by 1 of 2 agents · 19 s",
    );
    // Without server times, the slowest agent is the best estimate.
    expect(traceSummary({ plan: null, steps: [step({ seconds: 4 }), step({ agent: "x", seconds: 12.6 })], totalSeconds: null })).toBe(
      "Answered by 2 agents · 13 s",
    );
    expect(traceSummary({ plan: null, steps: [step({})], totalSeconds: null })).toBe("Answered by 1 agent · 8.1 s");
  });

  it("knows when agents are still working", () => {
    expect(agentsWorking({ plan: null, steps: [step({}), step({ status: "running" })], totalSeconds: null })).toBe(true);
    expect(agentsWorking({ plan: null, steps: [step({}), step({ status: "failed" })], totalSeconds: null })).toBe(false);
  });
});
