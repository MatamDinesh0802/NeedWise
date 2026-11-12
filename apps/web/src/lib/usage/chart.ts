/**
 * Shapes a usage summary for the charts: cost over time for the shared SVG line chart
 * (components/insights/analytics-chart.tsx), and ranked bars for the breakdown. Pure and tested.
 *
 * Dataviz rules applied here: one measure per axis (cost; requests and tokens stay in the tables),
 * one hue for ranked bars of nominal groups, and gaps in the series are real zeros only where the
 * period is known to be in range (days, calendar months).
 */

import type { ChartData } from "@/lib/analytics/chart-data";
import type { GroupBy, UsageGroup, UsageSummary } from "@/lib/usage/api";
import { expectedPeriods, INTERVAL_NOUNS, periodLabel } from "@/lib/usage/dates";
import { groupLabel } from "@/lib/usage/format";

const X_LABELS = { day: "Day", week: "Week", month: "Month" } as const;

/** Cost per period as a one-series line chart; null when there are no periods to draw. */
export function costChart(summary: Pick<UsageSummary, "from" | "to" | "interval" | "series">): ChartData | null {
  const byPeriod = new Map<string, number>();
  for (const point of summary.series) {
    const key = point.period.slice(0, 10);
    byPeriod.set(key, (byPeriod.get(key) ?? 0) + (Number.isFinite(point.cost_usd) ? point.cost_usd : 0));
  }
  const expected = expectedPeriods({ from: summary.from, to: summary.to }, summary.interval) ?? [];
  const periods = [...new Set([...expected, ...byPeriod.keys()])].sort();
  if (periods.length === 0) return null;
  return {
    type: "line",
    title: `Cost per ${INTERVAL_NOUNS[summary.interval]}`,
    xLabel: X_LABELS[summary.interval],
    categories: periods.map((period) => ({ key: period, label: periodLabel(period, summary.interval) })),
    series: [{ key: "cost_usd", label: "Cost", values: periods.map((p) => byPeriod.get(p) ?? 0), slot: 1 }],
    yFormat: "usd",
    percentScale: "points",
    folded: 0,
    omitted: [],
  };
}

export interface BreakdownRow {
  key: string;
  label: string;
  cost: number;
  share: number;
  requests: number;
  inputTokens: number;
  outputTokens: number;
  /** Bar length relative to the largest bar, 0…1. */
  fraction: number;
}

export interface Breakdown {
  /** What the bars measure: cost, or requests when nothing in the range had a price. */
  measure: "cost" | "requests";
  rows: BreakdownRow[];
}

/** Rows for the breakdown, largest first, with names (never ids). */
export function breakdown(groups: readonly UsageGroup[], groupBy: GroupBy): Breakdown {
  const measure = groups.some((g) => g.cost_usd > 0) ? "cost" : "requests";
  const value = (g: UsageGroup) => (measure === "cost" ? g.cost_usd : g.requests);
  const sorted = [...groups].sort((a, b) => value(b) - value(a));
  const max = Math.max(0, ...sorted.map(value));
  return {
    measure,
    rows: sorted.map((g) => ({
      key: g.key,
      label: groupLabel(g, groupBy),
      cost: g.cost_usd,
      share: g.share,
      requests: g.requests,
      inputTokens: g.input_tokens,
      outputTokens: g.output_tokens,
      fraction: max > 0 ? Math.max(0, value(g)) / max : 0,
    })),
  };
}

/** Bars past this many can't be compared at a glance; the table lists every group. */
export const MAX_BARS = 10;

/** The first `max` rows as bars, and how many more the table holds. */
export function visibleBars(rows: readonly BreakdownRow[], max = MAX_BARS): { bars: BreakdownRow[]; hidden: number } {
  return { bars: rows.slice(0, max), hidden: Math.max(0, rows.length - max) };
}
