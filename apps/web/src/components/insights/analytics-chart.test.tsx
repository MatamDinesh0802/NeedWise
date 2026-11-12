import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { shapeChart, type ChartData } from "@/lib/analytics/chart-data";
import { monthlyRevenue, topDrivers } from "@/test/analytics-fixtures";

import { AnalyticsChart } from "./analytics-chart";

function data(result: ReturnType<typeof shapeChart>): ChartData {
  if (result.kind !== "chart") throw new Error("expected a chart");
  return result.data;
}

const stacked = data(
  shapeChart({ type: "stacked_bar", query: "Q1", x: "month", y: ["revenue_eur"], series: "product_line", title: "Monthly revenue by product line", y_format: "eur" }, monthlyRevenue),
);
const line = data(shapeChart({ type: "line", query: "Q1", x: "month", y: ["revenue_eur"], series: "product_line", title: "Revenue trend", y_format: "eur" }, monthlyRevenue));
const bars = data(shapeChart({ type: "bar", query: "Q2", x: "name", y: ["growth_eur"], series: null, title: "Top drivers", y_format: "eur" }, topDrivers));

describe("AnalyticsChart", () => {
  it("is a labelled, keyboard-focusable figure with instructions", () => {
    const html = renderToStaticMarkup(<AnalyticsChart data={stacked} />);
    expect(html).toContain("<figure");
    expect(html).toContain("<figcaption");
    expect(html).toContain('role="group" aria-roledescription="chart" aria-label="Monthly revenue by product line"');
    expect(html).toContain('tabindex="0"');
    expect(html).toContain("Use the left and right arrow keys to read each month.");
    expect(html).toContain('aria-pressed="true"'); // Chart / Table toggle
    expect(html).toContain('aria-live="polite"');
  });

  it("draws a stacked bar per segment, with a legend for two series", () => {
    const html = renderToStaticMarkup(<AnalyticsChart data={stacked} />);
    expect(html.match(/<path d="M[^"]*" style="fill:var\(--viz-\d\)"/g)).toHaveLength(6); // 3 months × 2 product lines
    expect(html).toContain('aria-label="Legend"');
    expect(html).toContain("AMR-200");
    expect(html).toContain("var(--viz-1)");
    expect(html).toContain("var(--viz-2)");
    // Axis ticks in compact euros.
    expect(html).toContain("€2M");
  });

  it("draws one path per line series with markers", () => {
    const html = renderToStaticMarkup(<AnalyticsChart data={line} />);
    expect(html.match(/style="stroke:var\(--viz-\d\)"/g)).toHaveLength(2);
    expect(html.match(/<circle/g)).toHaveLength(6);
  });

  it("needs no legend for one series and labels only the largest bar", () => {
    const html = renderToStaticMarkup(<AnalyticsChart data={bars} />);
    expect(html).not.toContain('aria-label="Legend"');
    expect(html).toContain("€1.65M");
    expect(html).not.toContain("€1.32M");
  });
});
