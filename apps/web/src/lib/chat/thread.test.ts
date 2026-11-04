import { describe, expect, it } from "vitest";

import { answerFromMessage, answerFromRun } from "./answer";
import type { Message, Source } from "./api";
import { initialRunState } from "./run-reducer";
import { groupSources, hasStoredAnswer, questionForRun, runToAttach, threadItems } from "./thread";

export function message(overrides: Partial<Message>): Message {
  return {
    id: "m",
    role: "user",
    content: "",
    status: "complete",
    created_at: "2026-11-04T09:00:00Z",
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
  };
}

const q1 = message({ id: "u1", role: "user", content: "How many weeks?", run_id: "r1" });
const a1 = message({ id: "a1", role: "assistant", content: "16 weeks [S1].", run_id: "r1", search_query: "How many weeks?" });
const q2 = message({ id: "u2", role: "user", content: "and the 2025 draft?", run_id: "r2" });

describe("runToAttach", () => {
  it("attaches to the active run reported on load", () => {
    expect(runToAttach({ active_run_id: "r2" }, null)).toBe("r2");
  });
  it("does nothing when idle or already following that run", () => {
    expect(runToAttach({ active_run_id: null }, null)).toBeNull();
    expect(runToAttach({ active_run_id: "r2" }, "r2")).toBeNull();
  });
  it("switches to a newer active run", () => {
    expect(runToAttach({ active_run_id: "r3" }, "r2")).toBe("r3");
  });
});

describe("threadItems", () => {
  it("lists stored messages, then the pending question and the live answer", () => {
    const live = initialRunState("r2");
    const items = threadItems([q1, a1], live, q2);
    expect(items.map((i) => i.key)).toEqual(["u1", "a1", "u2", "live-r2"]);
  });

  it("doesn't duplicate the question once the refetched conversation has it", () => {
    const items = threadItems([q1, a1, q2], initialRunState("r2"), q2);
    expect(items.map((i) => i.key)).toEqual(["u1", "a1", "u2", "live-r2"]);
  });

  it("drops the live answer once the stored one exists", () => {
    const a2 = message({ id: "a2", role: "assistant", run_id: "r2" });
    expect(threadItems([q1, a1, q2, a2], initialRunState("r2"), q2).map((i) => i.key)).toEqual(["u1", "a1", "u2", "a2"]);
    expect(hasStoredAnswer([a2], "r2")).toBe(true);
  });

  it("finds the question behind a run (for Retry)", () => {
    expect(questionForRun([q1, a1, q2], "r2")).toBe("and the 2025 draft?");
    expect(questionForRun([q1], "missing")).toBeNull();
    expect(questionForRun([q1], null)).toBeNull();
  });
});

const src = (marker: string): Source => ({
  marker,
  document_id: `d-${marker}`,
  chunk_id: `c-${marker}`,
  collection_id: "c",
  title: marker,
  filename: "x.pdf",
  section: null,
  page: null,
  trust: "official",
  sensitivity: "internal",
});

describe("groupSources", () => {
  it("puts cited sources first, the rest under 'also searched', in marker order", () => {
    const groups = groupSources([src("S3"), src("S1"), src("S10"), src("S2")], "x [S10] y [S1]", []);
    expect(groups.cited.map((s) => s.marker)).toEqual(["S1", "S10"]);
    expect(groups.other.map((s) => s.marker)).toEqual(["S2", "S3"]);
  });

  it("also counts citation events and ignores duplicate markers", () => {
    const groups = groupSources([src("S1"), src("S1"), src("S2")], "no markers in text", [{ marker: "S2" }]);
    expect(groups.cited.map((s) => s.marker)).toEqual(["S2"]);
    expect(groups.other.map((s) => s.marker)).toEqual(["S1"]);
  });
});

describe("answer view models", () => {
  it("maps a live run", () => {
    const run = { ...initialRunState("r9"), phase: "streaming" as const, text: "Hi", searchQuery: "rewritten" };
    expect(answerFromRun(run)).toMatchObject({ key: "live-r9", phase: "streaming", text: "Hi", searchedFor: "rewritten", message: null });
    expect(answerFromRun({ ...run, phase: "connecting" }).phase).toBe("searching");
    expect(answerFromRun({ ...run, phase: "completed" }).phase).toBe("complete");
  });

  it("maps a stored message, showing the search query only when it was rewritten", () => {
    expect(answerFromMessage(a1, "How many weeks?").searchedFor).toBeNull();
    const rewritten = message({ ...a1, search_query: "2025 draft parental leave weeks" });
    expect(answerFromMessage(rewritten, "and the 2025 draft?")).toMatchObject({
      key: "a1",
      phase: "complete",
      searchedFor: "2025 draft parental leave weeks",
      message: rewritten,
    });
    expect(answerFromMessage(message({ ...a1, status: "failed" }), null).error).not.toBeNull();
  });
});
