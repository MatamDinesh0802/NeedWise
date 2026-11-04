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
