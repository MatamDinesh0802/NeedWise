/**
 * CSV export of query results (RFC 4180). Values are written raw, not formatted, so the file
 * re-imports as numbers: 5020000, not "€5.02M". Unit-tested.
 */

import type { Cell } from "@/lib/analytics/api";

/** Excel reads UTF-8 (the € sign, accents) only with a byte-order mark. */
const BOM = "﻿";

/**
 * Text starting with = + - @ (or a tab / carriage return) is a formula to spreadsheet apps.
 * Data comes from uploaded files, so such text is prefixed with ' (OWASP "CSV injection").
 * Numbers are JSON numbers here and never get the prefix, so -12 stays a number.
 */
function neutralize(text: string): string {
  return /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
}

export function csvField(value: Cell): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  if (typeof value === "boolean") return value ? "true" : "false";
  const text = neutralize(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(columns: readonly string[], rows: readonly (readonly Cell[])[]): string {
  const lines = [columns.map((c) => csvField(c)).join(","), ...rows.map((row) => columns.map((_, i) => csvField(row[i] ?? null)).join(","))];
  return `${BOM}${lines.join("\r\n")}\r\n`;
}

/** "insights-q1-monthly-revenue-trend.csv" from the question and query id. */
export function csvFilename(question: string, queryId: string): string {
  const slug = question
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  // At most 48 characters, cut at a word boundary.
  const short = slug.length > 48 ? slug.slice(0, 49).replace(/-[^-]*$/, "") : slug;
  return `insights-${queryId.toLowerCase()}${short ? `-${short}` : ""}.csv`;
}

/** Starts a browser download of `text`. Browser-only. */
export function downloadText(text: string, filename: string, type = "text/csv;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  // Revoke after the click has been handled.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
