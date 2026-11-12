/** Sorting for result tables. Framework-free and unit-tested. */

import type { Cell, ResultColumn } from "@/lib/analytics/api";
import { isNumericFormat, toNumber } from "@/lib/analytics/format";

export type SortDirection = "asc" | "desc";
export interface SortState {
  column: number;
  direction: SortDirection;
}

const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

function compareCells(a: Cell, b: Cell, numeric: boolean): number {
  if (numeric) {
    const x = toNumber(a);
    const y = toNumber(b);
    if (x !== null && y !== null) return x - y;
  }
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "boolean" && typeof b === "boolean") return Number(a) - Number(b);
  // ISO dates sort correctly as text.
  return collator.compare(String(a), String(b));
}

/**
 * Rows sorted by one column; the original order breaks ties (stable). Nulls always go last,
 * whatever the direction, so "missing" never looks like the smallest or largest value.
 */
export function sortRows<R extends readonly Cell[]>(rows: readonly R[], columns: readonly ResultColumn[], sort: SortState | null): R[] {
  if (!sort || !columns[sort.column]) return [...rows];
  const numeric = isNumericFormat(columns[sort.column].format) || /int|decimal|double|float|numeric|real/i.test(columns[sort.column].type);
  const sign = sort.direction === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const x = a.row[sort.column] ?? null;
      const y = b.row[sort.column] ?? null;
      if (x === null || y === null) return x === y ? a.index - b.index : x === null ? 1 : -1;
      return sign * compareCells(x, y, numeric) || a.index - b.index;
    })
    .map((entry) => entry.row);
}

/** Clicking a header: ascending first for text, descending first for numbers; a third click clears. */
export function nextSort(current: SortState | null, column: number, numeric: boolean): SortState | null {
  const first: SortDirection = numeric ? "desc" : "asc";
  if (!current || current.column !== column) return { column, direction: first };
  if (current.direction === first) return { column, direction: first === "asc" ? "desc" : "asc" };
  return null;
}

export function ariaSort(sort: SortState | null, column: number): "ascending" | "descending" | "none" {
  if (!sort || sort.column !== column) return "none";
  return sort.direction === "asc" ? "ascending" : "descending";
}
