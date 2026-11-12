"use client";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useMemo, useState } from "react";

import type { Cell, ResultColumn } from "@/lib/analytics/api";
import { formatCell, isNumericFormat, percentScale, toNumber, type PercentScale } from "@/lib/analytics/format";
import { ariaSort, nextSort, sortRows, type SortState } from "@/lib/analytics/table";
import { cn } from "@/lib/utils";

/** Query results, formatted by column; click a header to sort (numbers start largest first). */
export function ResultTable({ columns, rows, caption }: { columns: readonly ResultColumn[]; rows: readonly Cell[][]; caption: string }) {
  const [sort, setSort] = useState<SortState | null>(null);
  const sorted = useMemo(() => sortRows(rows, columns, sort), [rows, columns, sort]);
  // Percent columns are read as ratios or points per column (see percentScale).
  const scales = useMemo<PercentScale[]>(
    () => columns.map((c, i) => (c.format === "percent" ? percentScale(rows.map((r) => toNumber(r[i] ?? null))) : "fraction")),
    [columns, rows],
  );

  if (columns.length === 0) return <p className="text-sm text-muted-foreground">No columns.</p>;

  return (
    <div className="max-h-96 overflow-auto rounded-lg border">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}. Column headers sort the rows.</caption>
        <thead className="sticky top-0 z-[1] bg-muted/90 backdrop-blur">
          <tr>
            {columns.map((column, i) => {
              const numeric = isNumericFormat(column.format);
              const state = ariaSort(sort, i);
              const Icon = state === "ascending" ? ArrowUp : state === "descending" ? ArrowDown : ArrowUpDown;
              return (
                <th key={`${column.name}-${i}`} scope="col" aria-sort={state} className={cn("p-0 font-medium whitespace-nowrap", numeric ? "text-right" : "text-left")}>
                  <button
                    type="button"
                    onClick={() => setSort((current) => nextSort(current, i, numeric))}
                    className={cn(
                      "inline-flex w-full items-center gap-1 px-3 py-1.5 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset",
                      numeric && "flex-row-reverse",
                    )}
                    title={`${column.name} (${column.type})`}
                  >
                    <span className="font-mono text-xs">{column.name}</span>
                    <Icon className={cn("size-3 shrink-0", state === "none" ? "text-muted-foreground/50" : "text-foreground")} aria-hidden="true" />
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-4 text-center text-muted-foreground">
                No rows.
              </td>
            </tr>
          ) : (
            sorted.map((row, r) => (
              <tr key={r} className="border-t">
                {columns.map((column, c) => (
                  <td
                    key={c}
                    className={cn(
                      "px-3 py-1.5 whitespace-nowrap",
                      isNumericFormat(column.format) && "text-right tabular-nums",
                      (row[c] ?? null) === null && "text-muted-foreground",
                    )}
                  >
                    {formatCell(row[c] ?? null, column.format, scales[c])}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
