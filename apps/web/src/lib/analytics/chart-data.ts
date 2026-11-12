/**
 * Turns a chart spec plus the main query's rows into what the SVG charts draw: ordered categories
 * along x and one numeric series per line / bar colour. Framework-free and unit-tested.
 *
 * - `series` set: long format (one row per x and series value) is pivoted to one series each.
 * - otherwise: every `y` column in the first one's unit is a series. There is one value axis, so
 *   a measure in another unit (a count beside euros) is left to the table (`omitted`).
 * - Date categories are sorted by date; other categories keep the query's ORDER BY.
 * - More than 8 series: the 7 largest keep their colour and the rest fold into "Other", so a
 *   colour is never generated or reused (dataviz rule: no 9th hue).
 */

import type { Cell, ChartSpec, ResultColumn, ValueFormat } from "@/lib/analytics/api";
import { categoryLabels, cellText, humanize, measureLabel, parseIsoDate, percentScale, toNumber, type PercentScale } from "@/lib/analytics/format";

export const MAX_SERIES = 8;
/** Bars past this many categories can't be read; the table shows them instead. */
export const MAX_BAR_CATEGORIES = 60;

export type DrawnChartType = "line" | "bar" | "stacked_bar";

export interface ChartSeries {
  key: string;
  label: string;
  /** One value per category; null where the data has no value. */
  values: (number | null)[];
  /** Categorical palette slot, 1–8, fixed by position in the answer (never by rank after a filter). */
  slot: number;
}

export interface ChartData {
  type: DrawnChartType;
  title: string;
  xLabel: string;
  categories: { key: string; label: string }[];
  series: ChartSeries[];
  yFormat: ValueFormat;
  percentScale: PercentScale;
  /** How many series were folded into "Other" (0 when none). */
  folded: number;
  /** Measures the spec asked for that are in another unit than the first, so not drawn. */
  omitted: string[];
}

export type ShapedChart =
  | { kind: "chart"; data: ChartData }
  /** The spec asks for a table: no chart, and nothing went wrong. */
  | { kind: "table" }
  /** The chart can't be drawn from these rows; `reason` says why (the table still shows the data). */
  | { kind: "unavailable"; reason: string };

interface Rows {
  columns: readonly ResultColumn[];
  rows: readonly (readonly Cell[])[];
}

function keyOf(value: Cell): string {
  return value === null ? "\u0000null" : `${typeof value}:${typeof value === "object" ? JSON.stringify(value) : String(value)}`;
}

export function shapeChart(spec: ChartSpec, result: Rows): ShapedChart {
  if (spec.type === "table") return { kind: "table" };
  const index = (name: string) => result.columns.findIndex((c) => c.name === name);
  const xi = index(spec.x);
  const yis = spec.y.map(index);
  const si = spec.series ? index(spec.series) : -1;
  const missing = [
    xi < 0 ? spec.x : null,
    ...spec.y.filter((_, i) => yis[i] < 0),
    spec.series && si < 0 ? spec.series : null,
  ].filter((name): name is string => name !== null);
  if (missing.length > 0) {
    return { kind: "unavailable", reason: `The chart refers to ${missing.map((m) => `“${m}”`).join(", ")}, which ${missing.length === 1 ? "isn't a column" : "aren't columns"} of ${spec.query}.` };
  }
  if (yis.length === 0) return { kind: "unavailable", reason: "The chart doesn't say which column to plot." };
  const unit = result.columns[yis[0]].format;
  const measures = spec.y.map((name, i) => ({ name, index: yis[i] })).filter((m, i) => i === 0 || result.columns[m.index].format === unit);
  const omitted = spec.y.filter((name) => !measures.some((m) => m.name === name)).map(measureLabel);
  if (result.rows.length === 0) return { kind: "unavailable", reason: `${spec.query} returned no rows, so there is nothing to chart.` };

  // Categories in first-appearance order (dates sorted below).
  const categoryIndex = new Map<string, number>();
  const categoryValues: Cell[] = [];
  for (const row of result.rows) {
    const key = keyOf(row[xi] ?? null);
    if (!categoryIndex.has(key)) {
      categoryIndex.set(key, categoryValues.length);
      categoryValues.push(row[xi] ?? null);
    }
  }

  let series: { key: string; label: string; values: (number | null)[] }[];
  const blank = () => categoryValues.map(() => null as number | null);
  const add = (values: (number | null)[], at: number, value: number | null) => {
    if (value === null) return;
    values[at] = (values[at] ?? 0) + value; // repeated (x, series) rows add up
  };

  // Written from inside the closures below, so kept in an object (TS doesn't track closure writes).
  const problem: { column: string | null } = { column: null };
  const numeric = (cell: Cell | undefined, column: string): number | null => {
    if (cell === null || cell === undefined) return null;
    const n = toNumber(cell);
    if (n === null) problem.column ??= column;
    return n;
  };

  if (si >= 0) {
    const yi = yis[0];
    const byKey = new Map<string, { key: string; label: string; values: (number | null)[] }>();
    for (const row of result.rows) {
      const raw = row[si] ?? null;
      const key = keyOf(raw);
      let entry = byKey.get(key);
      if (!entry) {
        entry = { key, label: raw === null ? "(none)" : cellText(raw), values: blank() };
        byKey.set(key, entry);
      }
      add(entry.values, categoryIndex.get(keyOf(row[xi] ?? null))!, numeric(row[yi], spec.y[0]));
    }
    series = [...byKey.values()];
  } else {
    series = measures.map(({ name, index }) => {
      const values = blank();
      for (const row of result.rows) add(values, categoryIndex.get(keyOf(row[xi] ?? null))!, numeric(row[index], name));
      return { key: name, label: measureLabel(name), values };
    });
  }
  if (problem.column) return { kind: "unavailable", reason: `“${problem.column}” isn't numeric, so it can't be plotted.` };

  // Dates: chronological, whatever the SQL ordered by.
  let order = categoryValues.map((_, i) => i);
  if (categoryValues.every((v) => parseIsoDate(v) !== null)) {
    order = order.sort((a, b) => parseIsoDate(categoryValues[a])!.getTime() - parseIsoDate(categoryValues[b])!.getTime());
  }
  const labels = categoryLabels(order.map((i) => categoryValues[i]), spec.x);
  const categories = order.map((i, n) => ({ key: keyOf(categoryValues[i]), label: labels[n] }));
  series = series.map((s) => ({ ...s, values: order.map((i) => s.values[i]) }));

  if ((spec.type === "bar" || spec.type === "stacked_bar") && categories.length > MAX_BAR_CATEGORIES) {
    return {
      kind: "unavailable",
      reason: `${categories.length} bars are too many to read; the table below has every value.`,
    };
  }

  let folded = 0;
  if (series.length > MAX_SERIES) {
    const total = (s: { values: (number | null)[] }) => s.values.reduce<number>((sum, v) => sum + Math.abs(v ?? 0), 0);
    const keep = new Set(
      [...series]
        .sort((a, b) => total(b) - total(a))
        .slice(0, MAX_SERIES - 1)
        .map((s) => s.key),
    );
    const rest = series.filter((s) => !keep.has(s.key));
    folded = rest.length;
    const other = categories.map((_, i) => {
      const present = rest.map((s) => s.values[i]).filter((v): v is number => v !== null);
      return present.length > 0 ? present.reduce((a, b) => a + b, 0) : null;
    });
    series = [...series.filter((s) => keep.has(s.key)), { key: "\u0000other", label: `Other (${folded})`, values: other }];
  }

  const all = series.flatMap((s) => s.values);
  if (all.every((v) => v === null)) return { kind: "unavailable", reason: `${spec.query} has no values to plot.` };

  return {
    kind: "chart",
    data: {
      type: spec.type,
      title: spec.title,
      xLabel: humanize(spec.x),
      categories,
      series: series.map((s, i) => ({ ...s, slot: i + 1 })),
      yFormat: spec.y_format,
      percentScale: percentScale(all),
      folded,
      omitted: si >= 0 ? [] : omitted,
    },
  };
}

/** The value range a chart's y axis must cover. Stacked bars stack positives up and negatives down. */
export function valueExtent(data: ChartData): { min: number; max: number } {
  let min = 0;
  let max = 0;
  if (data.type === "stacked_bar") {
    data.categories.forEach((_, i) => {
      let up = 0;
      let down = 0;
      for (const s of data.series) {
        const v = s.values[i] ?? 0;
        if (v >= 0) up += v;
        else down += v;
      }
      max = Math.max(max, up);
      min = Math.min(min, down);
    });
  } else {
    for (const s of data.series) {
      for (const v of s.values) {
        if (v === null) continue;
        max = Math.max(max, v);
        min = Math.min(min, v);
      }
    }
  }
  return { min, max };
}

export interface StackSegment {
  seriesKey: string;
  slot: number;
  value: number;
  /** Bottom and top in data units. */
  from: number;
  to: number;
  /** The segment at the end of its stack (furthest from 0) gets the rounded data-end. */
  outermost: boolean;
}

/** Stacked bar segments for one category: positives stack up from 0, negatives down. */
export function stackSegments(data: ChartData, categoryIndex: number): StackSegment[] {
  let up = 0;
  let down = 0;
  const segments: StackSegment[] = [];
  for (const s of data.series) {
    const value = s.values[categoryIndex];
    if (value === null || value === 0) continue;
    if (value > 0) {
      segments.push({ seriesKey: s.key, slot: s.slot, value, from: up, to: up + value, outermost: false });
      up += value;
    } else {
      segments.push({ seriesKey: s.key, slot: s.slot, value, from: down, to: down + value, outermost: false });
      down += value;
    }
  }
  const lastUp = segments.findLast((seg) => seg.value > 0);
  const lastDown = segments.findLast((seg) => seg.value < 0);
  if (lastUp) lastUp.outermost = true;
  if (lastDown) lastDown.outermost = true;
  return segments;
}

/** Every category's values as table rows, for the chart's table view. */
export function chartTable(data: ChartData): { header: string[]; rows: { label: string; values: (number | null)[] }[] } {
  return {
    header: [data.xLabel, ...data.series.map((s) => s.label)],
    rows: data.categories.map((c, i) => ({ label: c.label, values: data.series.map((s) => s.values[i]) })),
  };
}
