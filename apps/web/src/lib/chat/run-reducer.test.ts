import { describe, expect, it } from "vitest";

import { compareEventIds, initialRunState, isTerminal, reduceRunEvent, type RunEvent, type RunState } from "./run-reducer";

const source = (marker: string, title: string) => ({
  marker,
  document_id: `doc-${marker}`,
  chunk_id: `chunk-${marker}`,
  collection_id: "c1",
  title,
  filename: `${title}.pdf`,
  section: "2. Parental leave > 2.1 Duration",
  page: 2,
  trust: "official",
  sensitivity: "internal",
  text: "Employees receive 16 weeks of paid parental leave.",
});

/** A full run as the API emits it (ids are Valkey stream ids). */
const RUN: RunEvent[] = [
  { id: "1730000000000-0", type: "run.started", data: { run_id: "r1", conversation_id: "c1" } },
  { id: "1730000000001-0", type: "query.rewritten", data: { query: "parental leave weeks 2026" } },
  { id: "1730000000002-0", type: "retrieval", data: { sources: [source("S1", "Leave Policy 2026"), source("S2", "HR FAQ")], withheld: 1 } },
  { id: "1730000000003-0", type: "token", data: { text: "You get " } },
  { id: "1730000000003-1", type: "token", data: { text: "16 weeks " } },
  { id: "1730000000004-0", type: "token", data: { text: "[S1]." } },
  { id: "1730000000005-0", type: "citation", data: { marker: "S1", document_id: "doc-S1", title: "Leave Policy 2026", section: "2.1", page: 2, trust: "official" } },
  { id: "1730000000005-1", type: "verification", data: { unknown_markers: [] } },
  {
    id: "1730000000006-0",
    type: "run.completed",
    data: { message_id: "m2", usage: { model: "claude-opus-5-5", input_tokens: 2417, output_tokens: 339, cost_usd: 0.017 }, abstained: false, withheld: 1 },
  },
];

const play = (events: RunEvent[], from: RunState = initialRunState("r1")) => events.reduce(reduceRunEvent, from);

describe("reduceRunEvent", () => {
  it("builds the whole answer from a run", () => {
    const steps = RUN.map((_, i) => play(RUN.slice(0, i + 1)).phase);
    expect(steps).toEqual(["searching", "searching", "searching", "streaming", "streaming", "streaming", "streaming", "streaming", "completed"]);

    const state = play(RUN);
    expect(state).toMatchObject({
      phase: "completed",
      searchQuery: "parental leave weeks 2026",
      text: "You get 16 weeks [S1].",
      withheld: 1,
      messageId: "m2",
      abstained: false,
      usage: { input_tokens: 2417, output_tokens: 339 },
      lastEventId: "1730000000006-0",
    });
    expect(state.sources.map((s) => s.marker)).toEqual(["S1", "S2"]);
    expect(state.sources[0].text).toContain("16 weeks"); // live sources carry text
    expect(state.citations.map((c) => c.marker)).toEqual(["S1"]);
    expect(isTerminal(state)).toBe(true);
  });

  it("does not duplicate tokens when a reconnect replays events (dedupe by event id)", () => {
    const partial = play(RUN.slice(0, 5)); // up to "16 weeks "
    expect(partial.text).toBe("You get 16 weeks ");
    // EventSource reconnects; a fresh attach replays everything from the start.
    const replayed = play(RUN, partial);
    expect(replayed.text).toBe("You get 16 weeks [S1].");
    expect(replayed.sources).toHaveLength(2);
    expect(replayed.citations).toHaveLength(1);
  });

  it("ignores a single duplicated frame", () => {
    const once = play(RUN.slice(0, 4));
    expect(reduceRunEvent(once, RUN[3])).toBe(once);
  });

  it("handles a cancelled run, keeping the partial text", () => {
    const state = play([...RUN.slice(0, 5), { id: "1730000000009-0", type: "run.cancelled", data: { message_id: "m9" } }]);
    expect(state).toMatchObject({ phase: "cancelled", text: "You get 16 weeks ", messageId: "m9" });
  });

  it("handles a failed run with its code and message", () => {
    const state = play([
      RUN[0],
      { id: "1730000000001-5", type: "run.failed", data: { code: "model_unavailable", message: "The language model is unavailable.", message_id: "m3" } },
    ]);
    expect(state).toMatchObject({
      phase: "failed",
      messageId: "m3",
      error: { code: "model_unavailable", message: "The language model is unavailable." },
    });
  });

  it("falls back to a generic failure message", () => {
    expect(play([{ id: "1-0", type: "run.failed", data: {} }]).error).toEqual({
      code: "internal",
      message: "Something went wrong while answering.",
    });
  });

  it("keeps the budget of a budget_exceeded failure, and its detail when there is no message", () => {
    const budget = {
      id: "b1",
      scope: "tenant",
      subject: null,
      period: "month",
      limit_usd: 500,
      spent_usd: 500.4,
      warn_at: 0.8,
      status: "exceeded",
      resets_at: "2026-12-01T00:00:00Z",
    };
    expect(play([{ id: "1-0", type: "run.failed", data: { code: "budget_exceeded", detail: "The monthly budget is used up.", budget } }]).error).toEqual({
      code: "budget_exceeded",
      message: "The monthly budget is used up.",
      budget,
    });
    // A malformed budget is dropped; the code and words stay.
    expect(play([{ id: "1-0", type: "run.failed", data: { code: "budget_exceeded", message: "Used up.", budget: { id: 3 } } }]).error).toEqual({
      code: "budget_exceeded",
      message: "Used up.",
    });
  });

  it("ignores events after the run ended, and unknown event types", () => {
    const done = play(RUN);
    expect(reduceRunEvent(done, { id: "1730000000099-0", type: "token", data: { text: "late" } })).toBe(done);
    const started = play([RUN[0]]);
    expect(reduceRunEvent(started, { id: "1730000000000-5", type: "something.new", data: {} }).text).toBe("");
  });

  it("records abstentions and verification problems", () => {
    const state = play([
      RUN[0],
      { id: "1730000000002-0", type: "retrieval", data: { sources: [], withheld: 0 } },
      { id: "1730000000003-0", type: "token", data: { text: "I couldn't find this." } },
      { id: "1730000000003-1", type: "verification", data: { unknown_markers: ["S7"] } },
      { id: "1730000000004-0", type: "run.completed", data: { message_id: "m1", usage: {}, abstained: true, withheld: 0 } },
    ]);
    expect(state).toMatchObject({ abstained: true, unknownMarkers: ["S7"], sources: [] });
  });

  it("applies events without ids (no dedupe possible) and ignores malformed payloads", () => {
    const state = play([
      { type: "run.started", data: null },
      { type: "token", data: { text: 42 } },
      { type: "token", data: { text: "ok" } },
    ]);
    expect(state.text).toBe("ok");
  });
});

describe("compareEventIds", () => {
  it("orders Valkey stream ids numerically, not as text", () => {
    expect(compareEventIds("1730000000010-0", "1730000000009-0")).toBe(1);
    expect(compareEventIds("1730000000009-10", "1730000000009-9")).toBeGreaterThan(0);
    expect(compareEventIds("1730000000009-2", "1730000000009-2")).toBe(0);
    expect(compareEventIds("5", "5-0")).toBe(0);
    expect(compareEventIds("b", "a")).toBe(1);
  });
});

describe("agents (plan and agent.step)", () => {
  const plan: RunEvent = {
    id: "1730000000001-0",
    type: "plan",
    data: {
      mode: "fan_out",
      routed_by: "planner",
      subtasks: [
        { agent: "engineering", goal: "How to restart the fleet controller" },
        { agent: "support", goal: "P1 response time for customers" },
        { agent: "safety", goal: "Minimum distance to a moving AMR-500" },
      ],
    },
  };
  const running = (agent: string, id: string): RunEvent => ({ id, type: "agent.step", data: { agent, status: "running", label: "goal" } });
  const done = (agent: string, id: string, findings: number, seconds: number): RunEvent => ({
    id,
    type: "agent.step",
    data: { agent, status: "done", label: "goal", findings, sources: findings + 1, seconds },
  });
  const failed = (agent: string, id: string): RunEvent => ({
    id,
    type: "agent.step",
    data: { agent, status: "failed", label: "The agent timed out after 90 s", findings: 0, sources: 0, seconds: 90 },
  });

  const RUN_WITH_AGENTS: RunEvent[] = [
    { id: "1730000000000-0", type: "run.started", data: {} },
    plan,
    running("engineering", "1730000000001-1"),
    running("support", "1730000000001-2"),
    running("safety", "1730000000001-3"),
    done("support", "1730000005000-0", 4, 4.2),
    done("engineering", "1730000008100-0", 6, 8.1),
    failed("safety", "1730000009000-0"),
    { id: "1730000009100-0", type: "retrieval", data: { sources: [], withheld: 0 } },
    { id: "1730000009200-0", type: "token", data: { text: "Restart it with fleetctl [S1]." } },
    { id: "1730000019000-0", type: "run.completed", data: { message_id: "m1", usage: {}, abstained: false, withheld: 0 } },
  ];

  it("tracks every agent in plan order while they finish in any order", () => {
    const state = play(RUN_WITH_AGENTS);
    expect(state.plan).toEqual({
      mode: "fan_out",
      routedBy: "planner",
      subtasks: expect.arrayContaining([expect.objectContaining({ agent: "support" })]),
    });
    expect(state.agents.map((a) => [a.agent, a.status, a.findings, a.seconds])).toEqual([
      ["engineering", "done", 6, 8.1],
      ["support", "done", 4, 4.2],
      ["safety", "failed", 0, 90],
    ]);
    expect(state.agents[0].goal).toBe("How to restart the fleet controller");
    expect(state.agents[2].label).toMatch(/timed out/);
    expect(state.text).toBe("Restart it with fleetctl [S1].");
    expect([state.startedAtMs, state.endedAtMs]).toEqual([1730000000000, 1730000019000]);
  });

  it("keeps agents 'searching' (not answered) until their done/failed arrives", () => {
    const mid = play(RUN_WITH_AGENTS.slice(0, 6));
    expect(mid.agents.map((a) => a.status)).toEqual(["running", "done", "running"]);
    expect(mid.phase).toBe("searching");
  });

  it("never goes backwards when 'done' arrives before 'running'", () => {
    const state = play([RUN_WITH_AGENTS[0], plan, done("support", "1730000002000-0", 3, 2), running("support", "1730000002000-1")]);
    expect(state.agents.find((a) => a.agent === "support")).toMatchObject({ status: "done", findings: 3, seconds: 2 });
  });

  it("merges steps that arrive before the plan, keeping plan order", () => {
    const state = play([RUN_WITH_AGENTS[0], done("safety", "1730000000000-5", 2, 1.5), plan]);
    expect(state.agents.map((a) => [a.agent, a.status])).toEqual([
      ["engineering", "pending"],
      ["support", "pending"],
      ["safety", "done"],
    ]);
    expect(state.agents[2].goal).toBe("Minimum distance to a moving AMR-500");
  });

  it("accepts agent events after retrieval and tokens too", () => {
    const state = play([
      RUN_WITH_AGENTS[0],
      { id: "1730000000001-0", type: "retrieval", data: { sources: [], withheld: 0 } },
      { id: "1730000000002-0", type: "token", data: { text: "Hi" } },
      plan,
      done("engineering", "1730000000004-0", 1, 1),
    ]);
    expect(state.phase).toBe("streaming");
    expect(state.agents.find((a) => a.agent === "engineering")?.status).toBe("done");
  });

  it("does not duplicate or regress anything when the whole run is replayed", () => {
    const once = play(RUN_WITH_AGENTS.slice(0, 7));
    const replayed = play(RUN_WITH_AGENTS, once);
    expect(replayed.agents).toHaveLength(3);
    expect(replayed.agents.map((a) => a.status)).toEqual(["done", "done", "failed"]);
    expect(replayed.text).toBe("Restart it with fleetctl [S1].");
  });

  it("records a plan without agent steps (single plans in auto mode)", () => {
    const state = play([
      RUN_WITH_AGENTS[0],
      { id: "1730000000001-0", type: "plan", data: { mode: "single", routed_by: "fast_path", subtasks: [{ agent: "engineering", goal: "rate limit" }] } },
    ]);
    expect(state.plan?.mode).toBe("single");
    expect(state.agents.map((a) => a.status)).toEqual(["pending"]);
  });

  it("ignores malformed agent events", () => {
    const base = play([RUN_WITH_AGENTS[0]]);
    const next = play(
      [
        { id: "1730000000001-0", type: "agent.step", data: { status: "done" } },
        { id: "1730000000002-0", type: "agent.step", data: { agent: "x", status: "exploded" } },
      ],
      base,
    );
    expect(next.agents).toEqual([]);
  });
});
