import { describe, expect, it } from "vitest";

import {
  categoryLabels,
  formatCell,
  formatCost,
  formatDay,
  formatElapsed,
  formatEur,
  formatNumber,
  formatPercent,
  formatValue,
  humanize,
  niceTicks,
  percentScale,
  toNumber,
} from "./format";

describe("formatEur", () => {
  it.each([
    [5020000, "€5,020,000"],
    [1520, "€1,520"],
    [1234.5, "€1,234.50"],
    [1234.0000001, "€1,234"],
    [-42000, "-€42,000"],
    [0, "€0"],
  ])("full %d → %s", (value, text) => {
    expect(formatEur(value, "full")).toBe(text);
  });

  it.each([
    [5020000, "€5.02M"],
    [1520000, "€1.52M"],
    [152000, "€152K"],
    [15200, "€15.2K"],
    [1520, "€1,520"], // under 10,000 stays exact
    [2_300_000_000, "€2.3B"],
    [-1650000, "-€1.65M"],
  ])("compact %d → %s", (value, text) => {
    expect(formatEur(value, "compact")).toBe(text);
  });
});

describe("formatNumber and formatPercent", () => {
  it("groups and rounds", () => {
    expect(formatNumber(1234567.891)).toBe("1,234,567.89");
    expect(formatNumber(1234567, "compact")).toBe("1.23M");
    expect(formatNumber(9999, "compact")).toBe("9,999");
  });

  it("writes ratios and points as percentages", () => {
    expect(formatPercent(0.425)).toBe("42.5%");
    expect(formatPercent(0.4, "fraction")).toBe("40%");
    expect(formatPercent(42.5, "points")).toBe("42.5%");
  });

  it("detects whether a percent column holds ratios or points", () => {
    expect(percentScale([0.2, 0.45, null, 1])).toBe("fraction");
    expect(percentScale([12.5, 40])).toBe("points");
    expect(percentScale([null])).toBe("points");
  });

  it("dispatches on the format", () => {
    expect(formatValue(5020000, "eur", "compact")).toBe("€5.02M");
    expect(formatValue(0.12, "percent")).toBe("12%");
    expect(formatValue(12, "percent", "full", "points")).toBe("12%");
    expect(formatValue(21, "number")).toBe("21");
  });
});

describe("toNumber", () => {
  it("accepts numbers and numeric strings only", () => {
    expect(toNumber(12)).toBe(12);
    expect(toNumber("1520.50")).toBe(1520.5);
    expect(toNumber("-3e2")).toBe(-300);
    expect(toNumber("12 units")).toBeNull();
    expect(toNumber("")).toBeNull();
    expect(toNumber(null)).toBeNull();
    expect(toNumber(true)).toBeNull();
    expect(toNumber(Number.NaN)).toBeNull();
  });
});

describe("formatCell", () => {
  it("formats by column format", () => {
    expect(formatCell(5020000, "eur")).toBe("€5,020,000");
    expect(formatCell("5020000.10", "eur")).toBe("€5,020,000.10");
    expect(formatCell(0.25, "percent")).toBe("25%");
    expect(formatCell(25, "percent", "points")).toBe("25%");
    expect(formatCell(null, "eur")).toBe("—");
    expect(formatCell(true, "text")).toBe("Yes");
    expect(formatCell("Bluefin Freight", "text")).toBe("Bluefin Freight");
  });

  it("shows midnight timestamps as dates and keeps real times", () => {
    expect(formatCell("2026-03-01", "date")).toBe("2026-03-01");
    expect(formatCell("2026-03-01T00:00:00Z", "date")).toBe("2026-03-01");
    expect(formatCell("2026-03-01T14:30:00Z", "date")).toBe("2026-03-01T14:30:00Z");
  });
});

describe("categoryLabels", () => {
  it("labels months, quarters and years", () => {
    expect(categoryLabels(["2026-01-01", "2026-02-01"], "month")).toEqual(["Jan 2026", "Feb 2026"]);
    expect(categoryLabels(["2026-07-01", "2026-10-01"], "quarter")).toEqual(["Q3 2026", "Q4 2026"]);
    expect(categoryLabels(["2025-01-01", "2026-01-01"], "year")).toEqual(["2025", "2026"]);
    expect(categoryLabels(["2026-03-05"], "invoice_date")).toEqual(["Mar 5, 2026"]);
  });

  it("leaves other values readable", () => {
    expect(categoryLabels(["Benelux", null, 3, false], "region")).toEqual(["Benelux", "(none)", "3", "No"]);
  });
});

describe("niceTicks", () => {
  it("covers the range with round steps from zero", () => {
    expect(niceTicks(0, 1_720_000)).toEqual([0, 500_000, 1_000_000, 1_500_000, 2_000_000]);
    expect(niceTicks(0, 100)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(0, 0.45)).toEqual([0, 0.1, 0.2, 0.3, 0.4, 0.5]);
  });

  it("includes negative ranges and degenerate input", () => {
    const ticks = niceTicks(-40, 100);
    expect(ticks[0]).toBeLessThanOrEqual(-40);
    expect(ticks.at(-1)).toBeGreaterThanOrEqual(100);
    expect(ticks).toContain(0);
    expect(niceTicks(0, 0)).toEqual([0, 1]);
    expect(niceTicks(5, 5)[0]).toBe(0);
  });
});

describe("formatDay", () => {
  it("reads a date as a calendar day in any time zone", () => {
    expect(formatDay("2026-09-30")).toBe("Sep 30, 2026");
    expect(formatDay(null)).toBe("—");
    expect(formatDay("soon")).toBe("—");
  });
});

describe("small formatters", () => {
  it("humanizes column names", () => {
    expect(humanize("revenue_eur")).toBe("Revenue eur");
    expect(humanize("month")).toBe("Month");
  });

  it("formats elapsed time and cost", () => {
    expect(formatElapsed(14)).toBe("14 ms");
    expect(formatElapsed(1500)).toBe("1.5 s");
    expect(formatElapsed(23_400)).toBe("23 s");
    expect(formatCost(0.0285)).toBe("$0.029");
    expect(formatCost(0.0012)).toBe("$0.0012");
    expect(formatCost(1.5)).toBe("$1.50");
    expect(formatCost(null)).toBe("cost not reported");
  });
});
