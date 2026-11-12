import { describe, expect, it } from "vitest";

import { monthlyRevenue } from "@/test/analytics-fixtures";

import { shapeChart, type ChartData } from "./chart-data";
import { barPath, chartGeometry, groupedBars, indexAt, labelStep, lastIndex, linePath, MAX_BAR, maxIndex, PLOT_HEIGHT } from "./chart-geometry";

function data(type: ChartData["type"]): ChartData {
  const shaped = shapeChart({ type, query: "Q1", x: "month", y: ["revenue_eur"], series: "product_line", title: "t", y_format: "eur" }, monthlyRevenue);
  if (shaped.kind !== "chart") throw new Error("expected a chart");
  return shaped.data;
}

describe("chartGeometry", () => {
  it("maps zero to the baseline and the top tick to the top of the plot", () => {
    const g = chartGeometry(data("stacked_bar"), 600);
    expect(g.ticks[0]).toBe(0);
    expect(g.y(0)).toBe(g.margin.top + PLOT_HEIGHT);
    expect(g.y(g.ticks.at(-1)!)).toBe(g.margin.top);
    expect(g.tickLabels.at(-1)).toBe("€2M");
    expect(g.height).toBe(g.margin.top + PLOT_HEIGHT + g.margin.bottom); // the x-axis band is inside the height
  });

  it("centres bars in equal slots and spreads line points edge to edge", () => {
    const bars = chartGeometry(data("bar"), 600);
    expect(bars.x(0)).toBeCloseTo(bars.margin.left + bars.band / 2);
    const line = chartGeometry(data("line"), 600);
    expect(line.x(0)).toBe(line.margin.left);
    expect(line.x(2)).toBeCloseTo(line.margin.left + line.plotWidth);
  });

  it("leaves room on the right for a line's end label", () => {
    expect(chartGeometry(data("line"), 600, "€1.72M").margin.right).toBeGreaterThan(chartGeometry(data("line"), 600).margin.right);
  });
});

describe("marks", () => {
  it("rounds only the data-end of a bar and keeps the base square", () => {
    const up = barPath(10, 200, 100, 20);
    expect(up.startsWith("M10,200V104Q10,100 14,100")).toBe(true);
    expect(up.endsWith("V200Z")).toBe(true);
    const down = barPath(10, 100, 180, 20);
    expect(down.startsWith("M10,100V176Q10,180 14,180")).toBe(true);
    expect(barPath(10, 100, 100, 20)).toBe("");
    expect(barPath(0, 100, 50, 10, false)).toBe("M0,100V50Q0,50 0,50H10Q10,50 10,50V100Z");
  });

  it("caps grouped bars at 24px with 2px gaps, centred in the slot", () => {
    const wide = groupedBars(300, 2);
    expect(wide.width).toBe(MAX_BAR);
    expect(wide.offsets).toEqual([-25, 1]);
    const narrow = groupedBars(20, 3);
    expect(narrow.width).toBeLessThan(MAX_BAR);
    expect(narrow.offsets[1] - narrow.offsets[0]).toBeCloseTo(narrow.width + 2);
  });

  it("breaks lines at missing values", () => {
    expect(linePath([1, 2, null, 4], (i) => i * 10, (v) => v)).toBe("M0,1L10,2M30,4");
    expect(linePath([null, null], (i) => i, (v) => v)).toBe("");
  });
});

describe("interaction helpers", () => {
  it("finds the nearest point or the slot under the pointer, clamped", () => {
    const g = chartGeometry(data("line"), 600);
    expect(indexAt(g.margin.left + g.band * 0.6, g, 3, "line")).toBe(1);
    expect(indexAt(-50, g, 3, "line")).toBe(0);
    expect(indexAt(5000, g, 3, "line")).toBe(2);
    const b = chartGeometry(data("bar"), 600);
    expect(indexAt(b.margin.left + b.band * 1.9, b, 3, "bar")).toBe(1);
  });

  it("thins labels that would overlap", () => {
    expect(labelStep(["Jan 2026", "Feb 2026"], 200)).toBe(1);
    expect(labelStep(Array.from({ length: 12 }, () => "Sep 2026"), 20)).toBeGreaterThan(1);
  });

  it("finds the largest and the last value", () => {
    expect(maxIndex([3, null, 9, 9, 1])).toBe(2);
    expect(maxIndex([null])).toBe(-1);
    expect(lastIndex([1, 2, null])).toBe(1);
    expect(lastIndex([])).toBe(-1);
  });
});
