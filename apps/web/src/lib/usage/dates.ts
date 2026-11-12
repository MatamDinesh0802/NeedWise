/**
 * Date ranges for the usage page: presets, custom ranges, the chart interval and period labels.
 * Dates are calendar days (YYYY-MM-DD) in UTC, the same days the API counts in, so a range
 * means the same thing for every viewer whatever their time zone. Framework-free and tested.
 */

import type { Interval } from "@/lib/usage/api";

export type RangePreset = "this_month" | "last_30_days" | "last_month" | "custom";

export interface DateRange {
  from: string;
  to: string;
}

export const RANGE_PRESETS: readonly { value: RangePreset; label: string }[] = [
  { value: "this_month", label: "This month" },
  { value: "last_30_days", label: "Last 30 days" },
  { value: "last_month", label: "Last month" },
  { value: "custom", label: "Custom range" },
];

const DAY_MS = 24 * 3600 * 1000;
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

const pad = (n: number) => String(n).padStart(2, "0");

/** A Date's UTC calendar day as YYYY-MM-DD. */
export function isoDay(date: Date): string {
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

/** "2026-11-05" → that UTC midnight; null for anything that isn't a real calendar day. */
export function parseDay(value: string): Date | null {
  if (!ISO_DAY.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime()) || isoDay(date) !== value ? null : date;
}

export function addDays(day: string, days: number): string {
  const date = parseDay(day);
  if (!date) return day;
  return isoDay(new Date(date.getTime() + days * DAY_MS));
}

/** Days in the range, both ends included (1 for a single day); 0 when invalid or reversed. */
export function rangeDays(range: DateRange): number {
  const from = parseDay(range.from);
  const to = parseDay(range.to);
  if (!from || !to || to < from) return 0;
  return Math.round((to.getTime() - from.getTime()) / DAY_MS) + 1;
}

/** The range a preset stands for, as of `now`. Custom ranges come from the inputs instead. */
export function presetRange(preset: Exclude<RangePreset, "custom">, now: Date = new Date()): DateRange {
  const today = isoDay(now);
  const year = now.getUTCFullYear();
  const month = now.getUTCMonth();
  if (preset === "this_month") return { from: isoDay(new Date(Date.UTC(year, month, 1))), to: today };
  if (preset === "last_30_days") return { from: addDays(today, -29), to: today };
  // last_month: the 1st to the last day of the previous calendar month.
  return { from: isoDay(new Date(Date.UTC(year, month - 1, 1))), to: isoDay(new Date(Date.UTC(year, month, 0))) };
}

/** Why a custom range can't be used, in words; null when it's fine. */
/** The longest range the API reports on (MAX_RANGE_DAYS in api/v1/usage.py). */
export const MAX_RANGE_DAYS = 400;

export function rangeError(range: DateRange): string | null {
  if (!range.from || !range.to) return "Choose both a start and an end date.";
  if (!parseDay(range.from) || !parseDay(range.to)) return "Enter dates as YYYY-MM-DD.";
  if (range.to < range.from) return "The end date is before the start date.";
  if (rangeDays(range) > MAX_RANGE_DAYS) return `Choose at most ${MAX_RANGE_DAYS} days.`;
  return null;
}

/**
 * Points on the cost chart: daily up to about six weeks, weekly up to about six months, monthly
 * beyond. Enough points to show a trend, few enough to read.
 */
export function intervalFor(range: DateRange): Interval {
  const days = rangeDays(range);
  if (days <= 45) return "day";
  if (days <= 186) return "week";
  return "month";
}

export const INTERVAL_NOUNS: Record<Interval, string> = { day: "day", week: "week", month: "month" };

/**
 * Every period start in the range, so periods without usage are drawn at $0. Weeks start on
 * Monday (UTC), as the API counts them (PostgreSQL's date_trunc('week')); the first one may
 * start before the range.
 */
export function expectedPeriods(range: DateRange, interval: Interval): string[] | null {
  const days = rangeDays(range);
  if (days === 0) return null;
  if (interval === "day") return Array.from({ length: days }, (_, i) => addDays(range.from, i));
  if (interval === "week") {
    const sinceMonday = (parseDay(range.from)!.getUTCDay() + 6) % 7;
    const weeks: string[] = [];
    for (let start = addDays(range.from, -sinceMonday); start <= range.to; start = addDays(start, 7)) weeks.push(start);
    return weeks;
  }
  const from = parseDay(range.from)!;
  const to = parseDay(range.to)!;
  const periods: string[] = [];
  for (let y = from.getUTCFullYear(), m = from.getUTCMonth(); Date.UTC(y, m, 1) <= to.getTime(); m++) {
    periods.push(isoDay(new Date(Date.UTC(y, m, 1))));
  }
  return periods;
}

const DAY_LABEL = new Intl.DateTimeFormat("en", { day: "numeric", month: "short", timeZone: "UTC" });
const DAY_YEAR_LABEL = new Intl.DateTimeFormat("en", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const MONTH_LABEL = new Intl.DateTimeFormat("en", { month: "short", year: "numeric", timeZone: "UTC" });

/** "Nov 3" (day), "Week of Nov 2" (week), "Nov 2026" (month). */
export function periodLabel(period: string, interval: Interval): string {
  const date = parseDay(period.slice(0, 10));
  if (!date) return period;
  if (interval === "month") return MONTH_LABEL.format(date);
  if (interval === "week") return `Week of ${DAY_LABEL.format(date)}`;
  return DAY_LABEL.format(date);
}

/** "Nov 1, 2026 – Nov 12, 2026": both ends in full, so a range across years is never ambiguous. */
export function rangeText(range: DateRange): string {
  const from = parseDay(range.from);
  const to = parseDay(range.to);
  if (!from || !to) return "";
  if (range.from === range.to) return DAY_YEAR_LABEL.format(from);
  return `${DAY_YEAR_LABEL.format(from)} – ${DAY_YEAR_LABEL.format(to)}`;
}
