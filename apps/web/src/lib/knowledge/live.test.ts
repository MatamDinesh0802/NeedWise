import { describe, expect, it } from "vitest";

import { LIVE_REFRESH_MAX_ERRORS, LIVE_REFRESH_MAX_MS, liveRefreshDecision, needsLiveRefresh } from "./live";

const docs = (...statuses: ("pending" | "processing" | "ready" | "failed")[]) => statuses.map((status) => ({ status }));
const base = { elapsedMs: 0, visible: true, consecutiveErrors: 0 };

describe("needsLiveRefresh", () => {
  it("is true only while something is pending or processing", () => {
    expect(needsLiveRefresh(docs("ready", "pending"))).toBe(true);
    expect(needsLiveRefresh(docs("processing"))).toBe(true);
    expect(needsLiveRefresh(docs("ready", "failed"))).toBe(false);
    expect(needsLiveRefresh([])).toBe(false);
  });
});

describe("liveRefreshDecision", () => {
  it("polls while documents are in flight and the tab is visible", () => {
    expect(liveRefreshDecision({ ...base, documents: docs("pending", "ready") })).toBe("poll");
  });

  it("stops once everything is ready or failed", () => {
    expect(liveRefreshDecision({ ...base, documents: docs("ready", "failed") })).toBe("stop-settled");
    expect(liveRefreshDecision({ ...base, documents: [] })).toBe("stop-settled");
  });

  it("pauses (doesn't stop) while the tab is hidden", () => {
    expect(liveRefreshDecision({ ...base, visible: false, documents: docs("processing") })).toBe("pause");
  });

  it("stops after repeated errors", () => {
    expect(liveRefreshDecision({ ...base, consecutiveErrors: LIVE_REFRESH_MAX_ERRORS - 1, documents: docs("pending") })).toBe("poll");
    expect(liveRefreshDecision({ ...base, consecutiveErrors: LIVE_REFRESH_MAX_ERRORS, documents: docs("pending") })).toBe("stop-errors");
  });

  it("stops after 10 minutes so a stuck document can't poll forever", () => {
    expect(liveRefreshDecision({ ...base, elapsedMs: LIVE_REFRESH_MAX_MS - 1, documents: docs("processing") })).toBe("poll");
    expect(liveRefreshDecision({ ...base, elapsedMs: LIVE_REFRESH_MAX_MS, documents: docs("processing") })).toBe("stop-timeout");
  });

  it("prefers 'settled' over other stop reasons, and stop reasons over pausing", () => {
    expect(liveRefreshDecision({ elapsedMs: LIVE_REFRESH_MAX_MS, visible: false, consecutiveErrors: 9, documents: docs("ready") })).toBe("stop-settled");
    expect(liveRefreshDecision({ elapsedMs: 0, visible: false, consecutiveErrors: 9, documents: docs("pending") })).toBe("stop-errors");
  });
});
