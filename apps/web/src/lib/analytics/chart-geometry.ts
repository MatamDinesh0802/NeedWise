/**
 * Geometry for the hand-written SVG charts: scales, axis ticks, bar paths with a rounded data-end,
 * line paths with gaps, and label thinning. Pure, so it is unit-tested without a DOM.
 *
 * Mark specs (dataviz skill): bars at most 24px thick with a 4px rounded data-end and a square
 * base; a 2px surface gap between touching marks; 2px lines; hairline, solid grid.
 */

import { valueExtent, type ChartData } from "@/lib/analytics/chart-data";
import { axisLabels, niceTicks } from "@/lib/analytics/format";

export const PLOT_HEIGHT = 240;
export const MAX_BAR = 24;
export const GAP = 2;
export const RADIUS = 4;
const CHAR_WIDTH = 6.4; // ~11-12px system sans
const AXIS_FONT_GAP = 8;
/** Rotated category labels (bars that would otherwise lose labels): angle, and longest text. */
export const LABEL_ANGLE = 35;
export const MAX_LABEL_CHARS = 20;
const LINE_HEIGHT = 14;

export interface Geometry {
  width: number;
  height: number;
  margin: { top: number; right: number; bottom: number; left: number };
  plotWidth: number;
  ticks: number[];
  tickLabels: string[];
  /** Data value → y pixel. */
  y: (value: number) => number;
  /** Category index → x pixel of its centre. */
  x: (index: number) => number;
  /** Width of one category's slot (bars) or the distance between points (lines). */
  band: number;
  /** Show every n-th category label so labels never overlap. */
  labelStep: number;
  /** Bar labels are drawn at an angle (and shortened), so every category keeps its label. */
  rotateLabels: boolean;
}

/** A category label as drawn under a rotated axis: at most MAX_LABEL_CHARS characters. */
export function shortLabel(label: string): string {
  return label.length > MAX_LABEL_CHARS ? `${label.slice(0, MAX_LABEL_CHARS - 1).trimEnd()}…` : label;
}

export function estimateTextWidth(text: string): number {
  return text.length * CHAR_WIDTH;
}

/** Every n-th label fits when n slots hold one label plus padding. */
export function labelStep(labels: readonly string[], slot: number): number {
  if (labels.length === 0 || slot <= 0) return 1;
  const widest = Math.max(...labels.map(estimateTextWidth)) + 12;
  return Math.max(1, Math.ceil(widest / slot));
}

export function chartGeometry(data: ChartData, width: number, endLabel: string | null = null): Geometry {
  const { min, max } = valueExtent(data);
  const ticks = niceTicks(min, max);
  const tickLabels = axisLabels(ticks, data.yFormat, data.percentScale);
  let left = Math.ceil(Math.max(...tickLabels.map(estimateTextWidth)) + AXIS_FONT_GAP + 4);
  let right = endLabel ? Math.ceil(estimateTextWidth(endLabel) + 12) : 12;
  if (data.type === "line" && data.categories.length > 1) {
    // The first and last x labels are centred on the plot's edges: leave room for their halves.
    left = Math.max(left, Math.ceil(estimateTextWidth(data.categories[0].label) / 2 + 4));
    right = Math.max(right, Math.ceil(estimateTextWidth(data.categories[data.categories.length - 1].label) / 2 + 4));
  }
  const margin = { top: 12, right, bottom: 28, left };
  const plotWidth = Math.max(40, width - margin.left - margin.right);
  const labels = data.categories.map((c) => c.label);
  // Bars name categories (customers, countries): thinning their labels leaves bars nobody can
  // name, so they turn to an angle instead, as long as one line of text fits per bar.
  const slot = plotWidth / Math.max(1, data.categories.length);
  const rotateLabels = data.type !== "line" && labelStep(labels, slot) > 1 && slot >= LINE_HEIGHT;
  if (rotateLabels) {
    const longest = Math.max(...labels.map((l) => estimateTextWidth(shortLabel(l))));
    margin.bottom = Math.ceil(Math.min(130, longest * Math.sin((LABEL_ANGLE * Math.PI) / 180) + 22));
  }
  const t0 = ticks[0];
  const t1 = ticks[ticks.length - 1];
  const y = (value: number) => margin.top + (1 - (value - t0) / (t1 - t0 || 1)) * PLOT_HEIGHT;
  const n = data.categories.length;
  let band: number;
  let x: (index: number) => number;
  if (data.type === "line") {
    band = n > 1 ? plotWidth / (n - 1) : plotWidth;
    x = (i) => margin.left + (n > 1 ? i * band : plotWidth / 2);
  } else {
    band = plotWidth / Math.max(1, n);
    x = (i) => margin.left + band * (i + 0.5);
  }
  return {
    width,
    height: margin.top + PLOT_HEIGHT + margin.bottom,
    margin,
    plotWidth,
    ticks,
    tickLabels,
    y,
    x,
    band,
    labelStep: rotateLabels ? 1 : labelStep(labels, band),
    rotateLabels,
  };
}

/**
 * A bar from `base` to `end` (pixels; end above base for positive values), `x` its left edge.
 * The data-end gets rounded corners, the base stays square. Zero-height bars draw nothing.
 */
export function barPath(x: number, base: number, end: number, width: number, rounded = true): string {
  const height = Math.abs(base - end);
  if (height < 0.5 || width <= 0) return "";
  const r = rounded ? Math.min(RADIUS, width / 2, height) : 0;
  const f = (v: number) => Number(v.toFixed(2));
  if (end < base) {
    // Grows up.
    return `M${f(x)},${f(base)}V${f(end + r)}Q${f(x)},${f(end)} ${f(x + r)},${f(end)}H${f(x + width - r)}Q${f(x + width)},${f(end)} ${f(x + width)},${f(end + r)}V${f(base)}Z`;
  }
  // Grows down (negative values).
  return `M${f(x)},${f(base)}V${f(end - r)}Q${f(x)},${f(end)} ${f(x + r)},${f(end)}H${f(x + width - r)}Q${f(x + width)},${f(end)} ${f(x + width)},${f(end - r)}V${f(base)}Z`;
}

/** Thickness and offsets of grouped bars within one category slot (≤ 24px each, 2px apart). */
export function groupedBars(band: number, count: number): { width: number; offsets: number[] } {
  const available = Math.max(4, band * 0.72);
  const width = Math.max(2, Math.min(MAX_BAR, (available - GAP * (count - 1)) / count));
  const total = width * count + GAP * (count - 1);
  return { width, offsets: Array.from({ length: count }, (_, i) => -total / 2 + i * (width + GAP)) };
}

/** SVG path data for one series; a null value breaks the line instead of drawing to zero. */
export function linePath(values: readonly (number | null)[], x: (i: number) => number, y: (v: number) => number): string {
  let d = "";
  let pen = false;
  values.forEach((value, i) => {
    if (value === null) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${Number(x(i).toFixed(2))},${Number(y(value).toFixed(2))}`;
    pen = true;
  });
  return d;
}

/** The category nearest to a pointer x (lines: nearest point; bars: the slot it's in). */
export function indexAt(px: number, geometry: Geometry, count: number, type: ChartData["type"]): number {
  if (count <= 1) return 0;
  const relative = px - geometry.margin.left;
  const raw = type === "line" ? Math.round(relative / geometry.band) : Math.floor(relative / geometry.band);
  return Math.min(count - 1, Math.max(0, raw));
}

/** Index of the largest value (for a single selective direct label), or -1. */
export function maxIndex(values: readonly (number | null)[]): number {
  let best = -1;
  values.forEach((v, i) => {
    if (v !== null && (best < 0 || v > (values[best] ?? -Infinity))) best = i;
  });
  return best;
}

/** Index of the last non-null value (where a line's end label goes), or -1. */
export function lastIndex(values: readonly (number | null)[]): number {
  for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null) return i;
  return -1;
}
