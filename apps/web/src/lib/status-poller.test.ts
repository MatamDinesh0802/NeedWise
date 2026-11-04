import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createStatusPoller, type StatusPoller } from "./status-poller";
import type { StatusFetchState } from "./system-status";

const payload = {
  status: "ok",
  product: "WorkMind AI",
  version: "0.0.1",
  environment: "test",
  components: [{ name: "postgres", status: "ok", latency_ms: 1.2, detail: null }],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** A fetch that never resolves until aborted, like a hung backend. */
const hangingFetch: typeof fetch = (_input, init) =>
  new Promise((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  });

describe("createStatusPoller", () => {
  let updates: StatusFetchState[];
  let poller: StatusPoller | undefined;

  beforeEach(() => {
    vi.useFakeTimers();
    updates = [];
  });

  afterEach(() => {
    poller?.stop();
    vi.useRealTimers();
  });

  it("fetches immediately and reports success", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(payload));
    poller = createStatusPoller({ onUpdate: (s) => updates.push(s), fetchImpl });
    await vi.waitFor(() => expect(updates).toHaveLength(1));

    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/system/status",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(updates[0]).toMatchObject({ kind: "success", data: { version: "0.0.1" } });
  });

  it("reports non-2xx responses as errors", async () => {
    poller = createStatusPoller({
      onUpdate: (s) => updates.push(s),
      fetchImpl: async () => jsonResponse({ detail: "bad gateway" }, 502),
    });
    await vi.waitFor(() => expect(updates).toHaveLength(1));
    expect(updates[0]).toMatchObject({ kind: "error", message: "HTTP 502" });
  });

  it("reports invalid payloads as errors", async () => {
    poller = createStatusPoller({
      onUpdate: (s) => updates.push(s),
      fetchImpl: async () => jsonResponse({ hello: "world" }),
    });
    await vi.waitFor(() => expect(updates).toHaveLength(1));
    expect(updates[0].kind).toBe("error");
  });

  it("times out hung requests", async () => {
    poller = createStatusPoller({
      onUpdate: (s) => updates.push(s),
      fetchImpl: hangingFetch,
      timeoutMs: 1_000,
    });
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.waitFor(() => expect(updates).toHaveLength(1));
    expect(updates[0]).toMatchObject({ kind: "error", message: "request timed out" });
  });

  it("polls on the interval but skips while not visible", async () => {
    let visible = true;
    const fetchImpl = vi.fn(async () => jsonResponse(payload));
    poller = createStatusPoller({
      onUpdate: (s) => updates.push(s),
      fetchImpl,
      intervalMs: 10_000,
      isVisible: () => visible,
    });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchImpl).toHaveBeenCalledTimes(2);

    visible = false;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("never reports after stop()", async () => {
    poller = createStatusPoller({ onUpdate: (s) => updates.push(s), fetchImpl: hangingFetch });
    poller.stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(updates).toHaveLength(0);
  });

  it("drops a superseded request in favour of the latest one", async () => {
    let call = 0;
    const fetchImpl: typeof fetch = (input, init) => {
      call += 1;
      return call === 1 ? hangingFetch(input, init) : Promise.resolve(jsonResponse(payload));
    };
    poller = createStatusPoller({ onUpdate: (s) => updates.push(s), fetchImpl });
    await poller.refresh();
    await vi.advanceTimersByTimeAsync(10);
    expect(updates).toHaveLength(1);
    expect(updates[0].kind).toBe("success");
  });
});
