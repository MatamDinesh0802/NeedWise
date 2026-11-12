import { describe, expect, it } from "vitest";

import { tenantSummary } from "@/test/usage-fixtures";

import { breakdown, costChart, visibleBars } from "./chart";

describe("costChart", () => {
  it("draws cost per day as one series, with days without usage as zero", () => {
    const data = costChart(tenantSummary)!;
    expect(data.type).toBe("line");
    expect(data.title).toBe("Cost per day");
    expect(data.yFormat).toBe("usd");
    expect(data.categories.map((c) => c.label)).toEqual(["Nov 1", "Nov 2", "Nov 3", "Nov 4", "Nov 5"]);
    expect(data.series).toHaveLength(1);
    expect(data.series[0]).toMatchObject({ label: "Cost", slot: 1, values: [3.5, 4.25, 0, 6, 5] });
  });

  it("draws every week of the range, weeks without usage as zero", () => {
    const data = costChart({
      from: "2026-08-01",
      to: "2026-11-12",
      interval: "week",
      series: [
        { period: "2026-11-09", cost_usd: 2, input_tokens: 0, output_tokens: 0, requests: 1 },
        { period: "2026-11-02", cost_usd: 1, input_tokens: 0, output_tokens: 0, requests: 1 },
      ],
    })!;
    expect(data.categories).toHaveLength(16);
    expect(data.categories[0].label).toBe("Week of Jul 27");
    expect(data.categories.slice(-2).map((c) => c.label)).toEqual(["Week of Nov 2", "Week of Nov 9"]);
    expect(data.series[0].values.slice(0, 3)).toEqual([0, 0, 0]);
    expect(data.series[0].values.slice(-2)).toEqual([1, 2]);
    expect(data.xLabel).toBe("Week");
  });

  it("draws weeks without data at zero, like days and months", () => {
    const data = costChart({ from: "2026-08-01", to: "2026-10-31", interval: "week", series: [] })!;
    expect(data.categories).toHaveLength(14);
    expect(new Set(data.series[0].values)).toEqual(new Set([0]));
  });

  it("fills whole months for a monthly chart", () => {
    const data = costChart({
      from: "2026-01-01",
      to: "2026-03-31",
      interval: "month",
      series: [{ period: "2026-02-01", cost_usd: 7, input_tokens: 0, output_tokens: 0, requests: 3 }],
    })!;
    expect(data.categories.map((c) => c.label)).toEqual(["Jan 2026", "Feb 2026", "Mar 2026"]);
    expect(data.series[0].values).toEqual([0, 7, 0]);
  });
});

describe("breakdown", () => {
  it("ranks groups by cost with names and relative bar lengths", () => {
    const { measure, rows } = breakdown(tenantSummary.groups, "department");
    expect(measure).toBe("cost");
    expect(rows.map((r) => r.label)).toEqual(["Engineering", "Finance", "People & HR"]);
    expect(rows[0].fraction).toBe(1);
    expect(rows[2].fraction).toBeCloseTo(3 / 9.5);
    expect(rows[1]).toMatchObject({ requests: 220, inputTokens: 400_000, outputTokens: 100_000, share: 0.3333 });
  });

  it("falls back to requests when nothing had a price", () => {
    const groups = tenantSummary.groups.map((g) => ({ ...g, cost_usd: 0, share: 0 }));
    const { measure, rows } = breakdown(groups, "model");
    expect(measure).toBe("requests");
    expect(rows[0]).toMatchObject({ label: "Engineering", fraction: 1 });
  });

  it("names features in words", () => {
    const { rows } = breakdown([{ key: "agent_test", label: "agent_test", cost_usd: 1, input_tokens: 1, output_tokens: 1, requests: 1, share: 1 }], "feature");
    expect(rows[0].label).toBe("Agent test console");
  });

  it("draws at most ten bars and says how many more the table has", () => {
    const rows = Array.from({ length: 13 }, (_, i) => ({ key: `k${i}`, label: `G${i}`, cost: 13 - i, share: 0, requests: 1, inputTokens: 0, outputTokens: 0, fraction: 0 }));
    const { bars, hidden } = visibleBars(rows);
    expect(bars).toHaveLength(10);
    expect(hidden).toBe(3);
    expect(visibleBars(rows.slice(0, 2)).hidden).toBe(0);
  });
});
