import { describe, expect, it, vi } from "vitest";

import type { EventSourceLike } from "@/lib/chat/run-stream";

import { initialProgress, progressAnnouncement, progressSteps, reduceProgress, type ProgressState } from "./progress";
import { createProgressStream } from "./progress-stream";

let seq = 0;
function apply(state: ProgressState, type: string, data: Record<string, unknown> = {}, id?: string): ProgressState {
  seq += 1;
  return reduceProgress(state, { id: id ?? `1700000000000-${seq}`, type, data });
}

const step = (state: ProgressState, name: string, data: Record<string, unknown> = {}) =>
  apply(state, "analytics.step", { step: name, ...data });

function answered(): ProgressState {
  let state = initialProgress();
  state = apply(state, "run.started");
  state = step(state, "planning", { datasets: ["uploads", "shop"] });
  state = step(state, "query_compiled", {
    sql: "SELECT nope FROM orders",
    dataset: "shop",
    dialect: "postgres",
    tool: "run_sql",
    purpose: "a typo",
  });
  state = step(state, "executing", { dataset: "shop" });
  state = step(state, "query_failed", { error: 'query_failed: Database error: column "nope" does not exist' });
  state = step(state, "query_compiled", { sql: "SELECT sum(amount_eur) FROM orders", dataset: "shop", dialect: "postgres", tool: "run_sql", purpose: "Shop revenue" });
  state = step(state, "executing");
  state = step(state, "query_done", { query: "Q1", dataset: "shop", rows: 1, truncated: false, elapsed_ms: 12 });
  state = step(state, "composing", { queries: 1 });
  state = step(state, "verifying", { verified: 1, total: 2 });
  state = step(state, "repairing", { unverified: ["€9M"] });
  state = step(state, "verifying", { verified: 1, total: 1 });
  state = step(state, "charting", { type: "stacked_bar" });
  return apply(state, "run.completed", { status: "answered" });
}

describe("reduceProgress", () => {
  it("follows the steps of an answer, query by query", () => {
    const state = answered();
    expect(state.phase).toBe("completed");
    expect(state.status).toBe("answered");
    expect(state.datasets).toEqual(["uploads", "shop"]);
    expect(state.queries.map((q) => [q.attempt, q.status, q.queryId, q.dataset, q.dialect])).toEqual([
      [1, "failed", null, "shop", "postgres"],
      [2, "done", "Q1", "shop", "postgres"],
    ]);
    expect(state.queries[0].error).toContain('column "nope"');
    expect(state.queries[1]).toMatchObject({ rows: 1, elapsedMs: 12, sql: "SELECT sum(amount_eur) FROM orders" });
    expect(state.verification).toEqual({ verified: 1, total: 1 });
    expect(state.repaired).toEqual(["€9M"]);
    expect(state.chartType).toBe("stacked_bar");
  });

  it("ignores replayed events and anything after the end", () => {
    let state = apply(initialProgress(), "run.started", {}, "1700000000000-5");
    state = apply(state, "analytics.step", { step: "query_compiled", sql: "SELECT 1" }, "1700000000000-6");
    const again = apply(state, "analytics.step", { step: "query_compiled", sql: "SELECT 1" }, "1700000000000-6");
    expect(again).toBe(state);
    const done = apply(state, "run.completed", { status: "no_access" }, "1700000000000-7");
    expect(done.status).toBe("no_access");
    expect(apply(done, "analytics.step", { step: "composing" }, "1700000000000-8")).toBe(done);
  });

  it("keeps why a run failed", () => {
    const state = apply(apply(initialProgress(), "run.started"), "run.failed", { code: "budget_exceeded", message: "The monthly budget is used up." });
    expect(state).toMatchObject({ phase: "failed", error: { code: "budget_exceeded", message: "The monthly budget is used up." } });
    expect(progressAnnouncement(state)).toBe("The monthly budget is used up.");
  });

  it("records a tool call refused before it compiled", () => {
    const state = step(step(initialProgress(), "planning", { datasets: ["uploads"] }), "query_failed", {
      error: "unknown_dataset: There is no dataset 'x'",
      tool: "run_sql",
      purpose: "",
    });
    expect(state.queries).toHaveLength(1);
    expect(state.queries[0]).toMatchObject({ status: "failed", sql: null, error: "unknown_dataset: There is no dataset 'x'" });
  });
});

describe("progressSteps", () => {
  it("lists planning, each query, writing, checking and the chart, with what happened", () => {
    const steps = progressSteps(answered());
    expect(steps.map((s) => [s.key, s.status])).toEqual([
      ["planning", "done"],
      ["query-1", "failed"],
      ["query-2", "done"],
      ["composing", "done"],
      ["verifying", "done"],
      ["charting", "done"],
    ]);
    expect(steps[0].detail).toBe("Datasets: uploads, shop");
    expect(steps[1].label).toBe("Query 1: a typo");
    expect(steps[1].detail).toBe('shop (PostgreSQL) · Database error: column "nope" does not exist. The assistant can correct it and try again.');
    expect(steps[2]).toMatchObject({ label: "Q1: Shop revenue", detail: "shop (PostgreSQL) · 1 row in 12 ms", sql: "SELECT sum(amount_eur) FROM orders" });
    expect(steps[3]).toMatchObject({ label: "Rewriting figures that didn't match", detail: "Not in the results: €9M" });
    expect(steps[4].detail).toBe("1 of 1 backed by a result");
    expect(steps[5].detail).toBe("stacked bar chart");
  });

  it("marks the current step active and the rest pending, without dataset names for one dataset", () => {
    let state = step(apply(initialProgress(), "run.started"), "planning", { datasets: ["uploads"] });
    expect(progressSteps(state).map((s) => s.status)).toEqual(["active", "pending", "pending", "pending"]);
    state = step(state, "query_compiled", { sql: "SELECT 1", dataset: "uploads", dialect: "duckdb", purpose: "Total" });
    const steps = progressSteps(state);
    expect(steps.map((s) => s.status)).toEqual(["done", "active", "pending", "pending", "pending"]);
    expect(steps[1].detail).toBe("Running with your access…");
    expect(steps[0].detail).toBeNull();
    expect(progressAnnouncement(state)).toBe("Query 1: Total: running.");
  });
});

class FakeEventSource implements EventSourceLike {
  static last: FakeEventSource | null = null;
  listeners = new Map<string, ((event: { data: string; lastEventId?: string }) => void)[]>();
  onerror: ((event: unknown) => void) | null = null;
  readyState = 1;
  closed = false;
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(type: string, listener: (event: { data: string; lastEventId?: string }) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emit(type: string, id: string, data: unknown) {
    for (const listener of this.listeners.get(type) ?? []) listener({ data: JSON.stringify(data), lastEventId: id });
  }
}

describe("createProgressStream", () => {
  it("opens the run's event stream once, reduces its events and closes after the end", () => {
    const onDone = vi.fn();
    const stream = createProgressStream("run 1", { createEventSource: (url) => new FakeEventSource(url), onDone });
    expect(FakeEventSource.last).toBeNull();
    stream.subscribe(() => {});
    stream.subscribe(() => {});
    const es = FakeEventSource.last!;
    expect(es.url).toBe("/api/v1/analytics/runs/run%201/events");
    es.emit("run.started", "1-1", { run_id: "run 1" });
    es.emit("analytics.step", "1-2", { step: "query_compiled", sql: "SELECT 1" });
    es.emit("analytics.step", "1-3", { step: "query_done", query: "Q1", rows: 1 });
    es.emit("run.completed", "1-4", { status: "answered" });
    expect(stream.getSnapshot()).toMatchObject({ phase: "completed", status: "answered" });
    expect(es.closed).toBe(true);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("reports a stream the browser gave up on", () => {
    FakeEventSource.last = null;
    const onDone = vi.fn();
    const stream = createProgressStream("r", { createEventSource: (url) => new FakeEventSource(url), onDone });
    stream.subscribe(() => {});
    const es = FakeEventSource.last!;
    es.readyState = 2;
    es.onerror?.({});
    expect(stream.getSnapshot()).toMatchObject({ phase: "failed", error: { code: "stream_lost" } });
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
