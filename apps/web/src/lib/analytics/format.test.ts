import { describe, expect, it } from "vitest";

import {
  axisLabels,
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
  measureLabel,
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

  it("writes points as percentages, and ratios when asked", () => {
    expect(formatPercent(42.5)).toBe("42.5%");
    expect(formatPercent(0.4, "fraction")).toBe("40%");
    expect(formatPercent(42.5, "points")).toBe("42.5%");
  });

  it("never guesses ratios from the values: growth of 0.6 points is 0.6%", () => {
    expect(percentScale([0.6, 0.9, -0.4])).toBe("points");
    expect(percentScale([0.42, 1.5])).toBe("points");
    expect(formatValue(0.6, "percent")).toBe("0.6%");
  });

  it("dispatches on the format", () => {
    expect(formatValue(5020000, "eur", "compact")).toBe("€5.02M");
    expect(formatValue(12, "percent")).toBe("12%");
    expect(formatValue(0.12, "percent", "full", "fraction")).toBe("12%");
    expect(formatValue(21, "number")).toBe("21");
  });

  it("labels one axis in one notation", () => {
    expect(axisLabels([0, 5000, 10000, 15000, 20000], "eur")).toEqual(["€0", "€5K", "€10K", "€15K", "€20K"]);
    expect(axisLabels([0, 5000, 10000], "number")).toEqual(["0", "5K", "10K"]);
    expect(axisLabels([0, 2000, 4000, 6000], "eur")).toEqual(["€0", "€2,000", "€4,000", "€6,000"]);
    expect(axisLabels([0, 20, 40], "percent")).toEqual(["0%", "20%", "40%"]);
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
    expect(formatCell(25, "percent")).toBe("25%");
    expect(formatCell(0.25, "percent", "fraction")).toBe("25%");
    expect(formatCell(null, "eur")).toBe("—");
    expect(formatCell(true, "text")).toBe("Yes");
    expect(formatCell("Bluefin Freight", "text")).toBe("Bluefin Freight");
  });

  it("writes lists, maps and structs as text, never as objects", () => {
    expect(formatCell(["C001", "C002", "C003"], "text")).toBe("C001, C002, C003");
    expect(formatCell({ open: 1, paid: 139, void: 3 }, "text")).toBe("open: 1, paid: 139, void: 3");
    expect(formatCell({ top: 213238 }, "number")).toBe("top: 213238");
    expect(formatCell([[1, 2], { a: null }], "text")).toBe("[1, 2], {a: null}");
    expect(formatCell([], "text")).toBe("");
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

  it("drops the unit from measure names, since the axis shows it", () => {
    expect(measureLabel("revenue_eur")).toBe("Revenue");
    expect(measureLabel("win_rate_pct")).toBe("Win rate");
    expect(measureLabel("deals")).toBe("Deals");
    expect(measureLabel("eur")).toBe("Eur");
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
