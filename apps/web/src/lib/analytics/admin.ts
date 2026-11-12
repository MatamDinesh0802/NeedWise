/**
 * Admin → Analytics helpers: table names, the semantic model's validation problems, and a
 * starting model generated from the uploaded tables. Framework-free and unit-tested.
 */

import { ApiError } from "@/lib/api";
import type { DatasetTable } from "@/lib/analytics/api";
import { describePath, parseValidationDetail } from "@/lib/validation-detail";

const TABLE_NAME = /^[a-z][a-z0-9_]*$/;
export const TABLE_NAME_MAX = 63;

export function tableNameError(name: string): string | null {
  if (!name) return "Add a table name.";
  if (name.length > TABLE_NAME_MAX) return `At most ${TABLE_NAME_MAX} characters.`;
  if (!TABLE_NAME.test(name)) return "Use lower-case letters, digits and underscores, starting with a letter (e.g. invoice_lines).";
  return null;
}

/** "Invoice Lines 2026.xlsx" → "invoice_lines_2026". */
export function suggestTableName(filename: string): string {
  const base = filename.replace(/\.[^.]+$/, "");
  let name = base
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (/^\d/.test(name)) name = `t_${name}`;
  return name.slice(0, TABLE_NAME_MAX).replace(/_+$/, "");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The problems in a 422 "invalid_model". The detail lists them: one per line (optionally bulleted),
 * separated by "; ", or as Pydantic's validation text. A `problems` array extension wins if sent.
 */
export function modelProblems(error: unknown): string[] {
  if (!(error instanceof ApiError) || error.code !== "invalid_model") return [];
  const extension = error.problem?.problems;
  if (Array.isArray(extension) && extension.length > 0) {
    return extension.map((p) => (typeof p === "string" ? p : isRecord(p) && typeof p.message === "string" ? p.message : JSON.stringify(p)));
  }
  const detail = (error.detail ?? "").trim();
  if (!detail) return ["The data model isn't valid."];
  // A YAML syntax error is one problem over several lines (the reason, where, the line and a
  // caret under the column): kept whole, with its indentation, for a preformatted block.
  if (detail.startsWith("not valid YAML:")) return [detail.replace(/in "<unicode string>", /g, "").replace(/\r\n/g, "\n")];
  if (/^\d+ validation errors? for /.test(detail)) {
    return parseValidationDetail(detail).map((e) => (e.path ? `${describePath(e.path)}: ${e.message}` : e.message));
  }
  const lines = detail
    .split(/\r?\n/)
    .map((line) => line.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, "").trim())
    .filter(Boolean);
  // A heading line ("The model has 2 problems:") followed by the list.
  const items = lines.length > 1 && lines[0].endsWith(":") ? lines.slice(1) : lines;
  if (items.length === 1 && items[0].includes("; ")) return items[0].split("; ").map((s) => s.trim()).filter(Boolean);
  return items;
}

/** Maps a dataset column type (DuckDB: VARCHAR, BIGINT, DECIMAL(18,2), DATE…) to a model column type. */
export function modelColumnType(type: string): "text" | "integer" | "decimal" | "date" | "boolean" {
  const t = type.toUpperCase();
  if (/BOOL/.test(t)) return "boolean";
  if (/DATE|TIMESTAMP/.test(t)) return "date";
  if (/DECIMAL|NUMERIC|DOUBLE|FLOAT|REAL/.test(t)) return "decimal";
  if (/INT/.test(t)) return "integer";
  return "text";
}

const quote = (text: string) => JSON.stringify(text); // a JSON string is a valid YAML scalar

/**
 * A starting semantic model for the uploaded tables: every table and column, its first date column
 * as the time column, open to the whole organization, and a row count per table as placeholder
 * metrics. Admins then describe the tables, add joins, real metrics and row policies.
 */
export function scaffoldModel(tables: readonly DatasetTable[]): string {
  const lines = [
    "# A starting point generated from the uploaded tables. Describe each table and column, add",
    "# joins, the metrics people ask about, and row policies, then save.",
    "name: Company data",
    "description: What this data covers and where it comes from.",
    "# data_through: 2026-09-30   # the last closed period",
    "access:",
    "  - org   # everyone in the organization; or department:finance, group:leadership, user:dan@acme.test",
    "tables:",
  ];
  for (const table of tables) {
    const timeColumn = table.columns.find((c) => modelColumnType(c.type) === "date");
    lines.push(`  ${table.name}:`, `    description: ${quote(`TODO: what one row of ${table.name} is`)}`);
    if (timeColumn) lines.push(`    time_column: ${timeColumn.name}`);
    lines.push("    columns:");
    for (const column of table.columns) lines.push(`      ${column.name}:`, `        type: ${modelColumnType(column.type)}`);
  }
  lines.push("joins: []   # e.g. - {left: invoice_lines.customer_id, right: customers.customer_id}", "dimensions: {}", "metrics:");
  for (const table of tables) {
    lines.push(
      `  ${`${table.name}_count`.slice(0, 63)}:`,
      `    table: ${table.name}`,
      "    sql: count(*)",
      `    description: ${quote(`Number of ${table.name.replace(/_/g, " ")}`)}`,
      "    format: number",
    );
  }
  lines.push("row_policies: []");
  return `${lines.join("\n")}\n`;
}
