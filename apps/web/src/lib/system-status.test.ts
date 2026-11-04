import { describe, expect, it } from "vitest";

import {
  formatLatency,
  parseSystemStatus,
  toStatusView,
  type SystemStatus,
} from "./system-status";

const healthy: SystemStatus = {
  status: "ok",
  product: "WorkMind AI",
  version: "0.0.1",
  environment: "dev",
  components: [
    { name: "keycloak", status: "ok", latency_ms: 12.4, detail: null },
    { name: "postgres", status: "ok", latency_ms: 2.1, detail: null },
    { name: "openfga", status: "ok", latency_ms: 3, detail: null },
    { name: "qdrant", status: "ok", latency_ms: 1.7, detail: null },
    { name: "valkey", status: "ok", latency_ms: 0.4, detail: null },
  ],
};

const checkedAt = new Date("2026-11-02T10:00:00Z");

describe("parseSystemStatus", () => {
  it("accepts a valid payload and normalizes optional fields", () => {
    const parsed = parseSystemStatus({
      ...healthy,
      components: [{ name: "postgres", status: "ok" }],
    });
    expect(parsed.components[0]).toEqual({
      name: "postgres",
      status: "ok",
      latency_ms: null,
      detail: null,
    });
  });

  it.each([
    ["non-object", "nope"],
    ["bad overall status", { ...healthy, status: "fine" }],
    ["missing version", { ...healthy, version: undefined }],
    ["components not an array", { ...healthy, components: {} }],
    ["bad component status", { ...healthy, components: [{ name: "qdrant", status: "up" }] }],
  ])("rejects %s", (_label, payload) => {
    expect(() => parseSystemStatus(payload)).toThrow();
  });
});

describe("toStatusView", () => {
  it("shows a neutral checking state while loading", () => {
    const view = toStatusView({ kind: "loading" });
    expect(view.tone).toBe("neutral");
    expect(view.components).toHaveLength(0);
  });

  it("reports all systems operational and orders components canonically", () => {
    const view = toStatusView({ kind: "success", data: healthy, checkedAt });
    expect(view.tone).toBe("ok");
    expect(view.headline).toBe("All systems operational");
    expect(view.summary).toBe("5 of 5 components healthy.");
    expect(view.components.map((c) => c.name)).toEqual([
      "postgres",
      "valkey",
      "qdrant",
      "openfga",
      "keycloak",
    ]);
    expect(view.components[0]).toMatchObject({ label: "PostgreSQL", latency: "2 ms", healthy: true });
    expect(view.version).toBe("0.0.1");
    expect(view.environment).toBe("dev");
  });

  it("reports degraded with a count of components down", () => {
    const data: SystemStatus = {
      ...healthy,
      status: "degraded",
      components: healthy.components.map((c) =>
        c.name === "qdrant" || c.name === "keycloak"
          ? { ...c, status: "down", latency_ms: null, detail: "ConnectError" }
          : c,
      ),
    };
    const view = toStatusView({ kind: "success", data, checkedAt });
    expect(view.tone).toBe("warning");
    expect(view.headline).toBe("Degraded");
    expect(view.summary).toBe("2 of 5 components down.");
    expect(view.components.find((c) => c.name === "qdrant")).toMatchObject({
      healthy: false,
      latency: "—",
      detail: "ConnectError",
    });
  });

  it("treats a component marked down as degraded even if the overall flag says ok", () => {
    const data: SystemStatus = {
      ...healthy,
      components: [{ name: "postgres", status: "down", latency_ms: null, detail: null }],
    };
    expect(toStatusView({ kind: "success", data, checkedAt }).tone).toBe("warning");
  });

  it("appends unknown components after the known ones", () => {
    const data: SystemStatus = {
      ...healthy,
      components: [
        { name: "zeta", status: "ok", latency_ms: 1, detail: null },
        { name: "alpha", status: "ok", latency_ms: 1, detail: null },
        { name: "postgres", status: "ok", latency_ms: 1, detail: null },
      ],
    };
    const view = toStatusView({ kind: "success", data, checkedAt });
    expect(view.components.map((c) => c.name)).toEqual(["postgres", "alpha", "zeta"]);
    expect(view.components[1].label).toBe("alpha");
  });

  it("shows API unreachable on fetch errors", () => {
    const view = toStatusView({ kind: "error", message: "HTTP 502", checkedAt });
    expect(view.tone).toBe("error");
    expect(view.headline).toBe("API unreachable");
    expect(view.summary).toContain("HTTP 502");
    expect(view.version).toBeNull();
  });
});

describe("formatLatency", () => {
  it.each([
    [null, "—"],
    [0.4, "<1 ms"],
    [2.4, "2 ms"],
    [999.4, "999 ms"],
    [1530, "1.5 s"],
  ])("formats %s as %s", (input, expected) => {
    expect(formatLatency(input)).toBe(expected);
  });
});
