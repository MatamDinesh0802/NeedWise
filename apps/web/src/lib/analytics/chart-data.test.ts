import { describe, expect, it } from "vitest";

import { monthlyRevenue, topDrivers } from "@/test/analytics-fixtures";

import type { ChartSpec, ResultColumn } from "./api";
import { chartTable, MAX_SERIES, shapeChart, stackSegments, valueExtent, type ChartData } from "./chart-data";

const spec = (overrides: Partial<ChartSpec> = {}): ChartSpec => ({
  type: "stacked_bar",
  query: "Q1",
  x: "month",
  y: ["revenue_eur"],
  series: "product_line",
  title: "Monthly revenue",
  y_format: "eur",
  ...overrides,
});

function chart(result: ReturnType<typeof shapeChart>): ChartData {
  if (result.kind !== "chart") throw new Error(`expected a chart, got ${JSON.stringify(result)}`);
  return result.data;
}

describe("shapeChart", () => {
  it("pivots long rows into one series per value, with dates in order", () => {
    const data = chart(shapeChart(spec(), monthlyRevenue));
    expect(data.categories.map((c) => c.label)).toEqual(["Jan 2026", "Feb 2026", "Mar 2026"]);
    expect(data.series.map((s) => [s.label, s.slot])).toEqual([
      ["AMR-200", 1],
      ["AMR-500", 2],
    ]);
    expect(data.series[0].values).toEqual([380000, 410000, 395000]);
    expect(data.series[1].values).toEqual([1150000, 1210000, 1320000]);
    expect(data.xLabel).toBe("Month");
    expect(data.folded).toBe(0);
  });

  it("draws only the measures in the first one's unit, and says which it left out", () => {
    const columns: ResultColumn[] = [
      { name: "month", type: "DATE", format: "date" },
      { name: "revenue", type: "DOUBLE", format: "eur" },
      { name: "customers_billed", type: "BIGINT", format: "number" },
    ];
    const rows = [
      ["2026-01-01", 3720000, 18],
      ["2026-02-01", 3860000, 19],
    ];
    const data = chart(shapeChart(spec({ type: "line", y: ["revenue", "customers_billed"], series: null }), { columns, rows }));
    expect(data.series.map((s) => s.label)).toEqual(["Revenue"]);
    expect(data.omitted).toEqual(["Customers billed"]);
  });

  it("uses each y column as a series when there is no series column, keeping query order", () => {
    const data = chart(shapeChart(spec({ type: "bar", x: "name", y: ["growth_eur"], series: null, query: "Q2" }), topDrivers));
    expect(data.categories.map((c) => c.label)).toEqual(["Bluefin Freight", "Polaris Parcel", "Nordlicht \"Logistik\", GmbH"]);
    expect(data.series).toHaveLength(1);
    expect(data.series[0]).toMatchObject({ label: "Growth", values: [1650000, 1320000, 410000.5], slot: 1 });
  });

  it("adds up repeated x/series rows and leaves gaps as null", () => {
    const columns: ResultColumn[] = [
      { name: "region", type: "text", format: "text" },
      { name: "segment", type: "text", format: "text" },
      { name: "revenue", type: "decimal", format: "eur" },
    ];
    const rows = [
      ["DACH", "3PL", 10],
      ["DACH", "3PL", 5],
      ["Nordics", "Parcel", 7],
    ];
    const data = chart(shapeChart(spec({ type: "bar", x: "region", y: ["revenue"], series: "segment" }), { columns, rows }));
    expect(data.series.map((s) => s.values)).toEqual([
      [15, null],
      [null, 7],
    ]);
  });

  it("reads numeric strings (decimals sent as text)", () => {
    const rows = monthlyRevenue.rows.map((r) => [r[0], r[1], String(r[2])]);
    expect(chart(shapeChart(spec(), { columns: monthlyRevenue.columns, rows })).series[0].values[0]).toBe(380000);
  });

  it("returns a table for table specs, and explains charts it can't draw", () => {
    expect(shapeChart(spec({ type: "table" }), monthlyRevenue)).toEqual({ kind: "table" });
    const missing = shapeChart(spec({ y: ["profit"] }), monthlyRevenue);
    expect(missing).toMatchObject({ kind: "unavailable" });
    expect(missing.kind === "unavailable" && missing.reason).toContain("“profit”");
    expect(shapeChart(spec(), { columns: monthlyRevenue.columns, rows: [] })).toMatchObject({ kind: "unavailable" });
    const text = shapeChart(spec({ y: ["product_line"], series: null }), monthlyRevenue);
    expect(text.kind === "unavailable" && text.reason).toContain("isn't numeric");
  });

  it("refuses bar charts with too many categories", () => {
    const columns: ResultColumn[] = [
      { name: "customer", type: "text", format: "text" },
      { name: "revenue", type: "decimal", format: "eur" },
    ];
    const rows = Array.from({ length: 80 }, (_, i) => [`C${i}`, i]);
    expect(shapeChart(spec({ type: "bar", x: "customer", y: ["revenue"], series: null }), { columns, rows }).kind).toBe("unavailable");
    expect(shapeChart(spec({ type: "line", x: "customer", y: ["revenue"], series: null }), { columns, rows }).kind).toBe("chart");
  });

  it("folds series past eight into Other instead of inventing colours", () => {
    const columns: ResultColumn[] = [
      { name: "month", type: "date", format: "date" },
      { name: "customer", type: "text", format: "text" },
      { name: "revenue", type: "decimal", format: "eur" },
    ];
    const rows = Array.from({ length: 10 }, (_, i) => ["2026-01-01", `C${i}`, (i + 1) * 100]);
    const data = chart(shapeChart(spec({ x: "month", y: ["revenue"], series: "customer" }), { columns, rows }));
    expect(data.series).toHaveLength(MAX_SERIES);
    expect(data.folded).toBe(3);
    expect(data.series.at(-1)).toMatchObject({ label: "Other (3)", values: [100 + 200 + 300], slot: 8 });
    // The largest seven keep their place in the answer's order.
    expect(data.series.slice(0, 7).map((s) => s.label)).toEqual(["C3", "C4", "C5", "C6", "C7", "C8", "C9"]);
    expect(new Set(data.series.map((s) => s.slot)).size).toBe(MAX_SERIES);
  });
});

describe("stacking", () => {
  const data = chart(shapeChart(spec(), monthlyRevenue));

  it("stacks positive values up from zero and marks the outer segment", () => {
    const segments = stackSegments(data, 0);
    expect(segments.map((s) => [s.from, s.to, s.outermost])).toEqual([
      [0, 380000, false],
      [380000, 1530000, true],
    ]);
    expect(valueExtent(data)).toEqual({ min: 0, max: 1715000 });
  });

  it("stacks negatives down, separately", () => {
    const mixed: ChartData = { ...data, series: [{ ...data.series[0], values: [100, -50, 10] }, { ...data.series[1], values: [-20, -30, 5] }] };
    expect(stackSegments(mixed, 1).map((s) => [s.from, s.to, s.outermost])).toEqual([
      [0, -50, false],
      [-50, -80, true],
    ]);
    expect(valueExtent(mixed)).toEqual({ min: -80, max: 100 });
    expect(valueExtent({ ...mixed, type: "bar" })).toEqual({ min: -50, max: 100 });
  });
});

describe("chartTable", () => {
  it("lists every category and series for the table view", () => {
    const table = chartTable(chart(shapeChart(spec(), monthlyRevenue)));
    expect(table.header).toEqual(["Month", "AMR-200", "AMR-500"]);
    expect(table.rows[2]).toEqual({ label: "Mar 2026", values: [395000, 1320000] });
  });
});
