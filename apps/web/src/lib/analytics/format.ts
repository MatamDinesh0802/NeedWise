/**
 * Number and label formatting for analytics results and charts. Framework-free and unit-tested.
 *
 * Two styles: "full" for table cells (exact, grouped: €5,020,000 or €1,234.56) and "compact" for
 * axis ticks and chart labels (€5.02M, €152K, €1,520). Under 10,000 compact stays exact, because
 * "€1.52K" is harder to read than "€1,520".
 */

import type { Cell, ColumnFormat, ValueFormat } from "@/lib/analytics/api";

export type NumberStyle = "full" | "compact";

/**
 * How a percent column is written. The contract only says "percent", and SQL writes it both
 * ways: a ratio (0.42) or points (42.0). See percentScale().
 */
export type PercentScale = "fraction" | "points";

const LOCALE = "en";
const COMPACT_FROM = 10_000;

const eurFull = new Intl.NumberFormat(LOCALE, { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const eurCents = new Intl.NumberFormat(LOCALE, {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const eurCompact = new Intl.NumberFormat(LOCALE, {
  style: "currency",
  currency: "EUR",
  notation: "compact",
  maximumSignificantDigits: 3,
});
const numFull = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 });
const numCompact = new Intl.NumberFormat(LOCALE, { notation: "compact", maximumSignificantDigits: 3 });
const pctFormat = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 });

/** A JSON row value as a number: numbers, and numeric strings (decimals can arrive as text). */
export function toNumber(value: Cell | undefined): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "" && /^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(value.trim())) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function formatEur(value: number, style: NumberStyle = "full"): string {
  if (style === "compact" && Math.abs(value) >= COMPACT_FROM) return eurCompact.format(value);
  if (style === "full" && !Number.isInteger(value) && Math.abs(value) < 1e12) {
    // Cents matter in a table; a rounded-away fraction like 1234.5000001 doesn't.
    return Math.abs(value - Math.round(value)) < 0.005 ? eurFull.format(value) : eurCents.format(value);
  }
  return eurFull.format(value);
}

export function formatNumber(value: number, style: NumberStyle = "full"): string {
  if (style === "compact" && Math.abs(value) >= COMPACT_FROM) return numCompact.format(value);
  return numFull.format(value);
}

/** Whether a percent column holds ratios (all within ±1) or points (anything larger). */
export function percentScale(values: readonly (number | null)[]): PercentScale {
  const present = values.filter((v): v is number => v !== null);
  return present.length > 0 && present.every((v) => Math.abs(v) <= 1) ? "fraction" : "points";
}

export function formatPercent(value: number, scale: PercentScale = "fraction"): string {
  const points = scale === "fraction" ? value * 100 : value;
  return `${pctFormat.format(points)}%`;
}

export function formatValue(value: number, format: ValueFormat, style: NumberStyle = "full", scale: PercentScale = "fraction"): string {
  if (format === "eur") return formatEur(value, style);
  if (format === "percent") return formatPercent(value, scale);
  return formatNumber(value, style);
}

/** True for formats whose cells are right-aligned numbers. */
export function isNumericFormat(format: ColumnFormat): format is ValueFormat {
  return format === "eur" || format === "number" || format === "percent";
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$/;
const MIDNIGHT = /^\d{4}-\d{2}-\d{2}(?:[T ]00:00(?::00(?:\.0+)?)?(?:Z|[+-]00:?00)?)?$/;

/** A table cell, as text. Dates stay ISO (unambiguous and sortable); null is an em dash. */
export function formatCell(value: Cell, format: ColumnFormat, scale: PercentScale = "fraction"): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (isNumericFormat(format)) {
    const number = toNumber(value);
    return number === null ? String(value) : formatValue(number, format, "full", scale);
  }
  // Midnight timestamps are dates; anything else keeps its time.
  if (format === "date" && typeof value === "string" && MIDNIGHT.test(value)) return value.slice(0, 10);
  if (typeof value === "number") return formatNumber(value);
  return value;
}

const MONTH = new Intl.DateTimeFormat(LOCALE, { month: "short", year: "numeric", timeZone: "UTC" });
const DAY = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/** Parses "2026-03-01" (or a timestamp) as a UTC date; null for anything else. */
export function parseIsoDate(value: Cell): Date | null {
  if (typeof value !== "string" || !ISO_DATE.test(value)) return null;
  const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "2026-09-30" → "Sep 30, 2026", read as a calendar day (UTC), so no time zone shifts it a day. */
export function formatDay(value: string | null): string {
  const date = parseIsoDate(value);
  return date ? DAY.format(date) : "—";
}

/**
 * Labels for chart categories. Dates follow the grain the column name or the values imply:
 * "quarter" → "Q3 2026", "year" → "2026", first-of-month → "Mar 2026", otherwise "Mar 5, 2026".
 */
export function categoryLabels(values: readonly Cell[], column: string): string[] {
  const dates = values.map(parseIsoDate);
  if (values.length > 0 && dates.every((d) => d !== null)) {
    const name = column.toLowerCase();
    const all = dates as Date[];
    if (name.includes("quarter")) return all.map((d) => `Q${Math.floor(d.getUTCMonth() / 3) + 1} ${d.getUTCFullYear()}`);
    if (name.includes("year") && all.every((d) => d.getUTCMonth() === 0 && d.getUTCDate() === 1)) {
      return all.map((d) => String(d.getUTCFullYear()));
    }
    if (all.every((d) => d.getUTCDate() === 1)) return all.map((d) => MONTH.format(d));
    return all.map((d) => DAY.format(d));
  }
  return values.map((v) => (v === null ? "(none)" : typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)));
}

/** "revenue_eur" → "Revenue eur". Column names are identifiers; this only makes them readable. */
export function humanize(name: string): string {
  const spaced = name.replace(/_+/g, " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : name;
}

/** Clean axis ticks (steps of 1, 2 or 5 × 10^n) covering [min, max], including 0 when in range. */
export function niceTicks(min: number, max: number, target = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0];
  if (min === max) {
    if (min === 0) return [0, 1];
    return min > 0 ? niceTicks(0, min, target) : niceTicks(min, 0, target);
  }
  const span = max - min;
  const rough = span / Math.max(1, target);
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => span / s <= target + 0.5) ?? 10 * magnitude;
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let value = start; value <= end + step / 2; value += step) {
    // Avoid 0.30000000000000004-style ticks.
    ticks.push(Number(value.toPrecision(12)));
  }
  return ticks;
}

/** "1,234 ms" → "1.2 s" style elapsed time. */
export function formatElapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

/** "$0.0123" for small model costs; null means the provider didn't report a price. */
export function formatCost(usd: number | null): string {
  if (usd === null || !Number.isFinite(usd)) return "cost not reported";
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toPrecision(2)}`;
  return `$${usd.toFixed(usd < 1 ? 3 : 2)}`;
}

export function formatCount(n: number): string {
  return numFull.format(n);
}
