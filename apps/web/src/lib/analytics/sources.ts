/**
 * Admin › Analytics › Databases: the connection form (checks that match the API's), a status
 * line per source, and a starting semantic model written from the tables an admin picks in the
 * source's catalog. Framework-free and unit-tested.
 */

import { slugError, suggestSlug } from "@/lib/slug";

import type { CatalogTable, SourceKind, SqlSource, SqlSourceInput, TlsMode } from "./api";
import { TABLE_NAME_MAX } from "./admin";

export const DEFAULT_PORTS: Record<SourceKind, number> = { postgres: 5432, mysql: 3306 };
export const KIND_LABELS: Record<SourceKind, string> = { postgres: "PostgreSQL", mysql: "MySQL" };
export const TLS_LABELS: Record<TlsMode, string> = {
  "verify-full": "Verify the certificate and host name",
  require: "Encrypt, don't verify the certificate",
  disable: "No TLS (only inside a trusted network)",
};

export interface SourceForm {
  kind: SourceKind;
  name: string;
  key: string;
  host: string;
  /** Empty: the kind's usual port. */
  port: string;
  database: string;
  username: string;
  password: string;
  tls_mode: TlsMode;
  tls_ca: string;
  /** Comma- or space-separated. */
  schemas: string;
}

export function emptySourceForm(kind: SourceKind = "postgres"): SourceForm {
  return {
    kind,
    name: "",
    key: "",
    host: "",
    port: "",
    database: "",
    username: "",
    password: "",
    tls_mode: "verify-full",
    tls_ca: "",
    schemas: kind === "postgres" ? "public" : "",
  };
}

const SCHEMA = /^[A-Za-z_][A-Za-z0-9_$]{0,62}$/;
const HOST = /^[A-Za-z0-9.:[\]_-]{1,253}$/;

export function parseSchemas(text: string): string[] {
  return [...new Set(text.split(/[\s,]+/).filter(Boolean))];
}

export type SourceFormErrors = Partial<Record<keyof SourceForm, string>>;

/** What to fix before sending, as the API would refuse it. Empty when the form is fine. */
export function sourceFormErrors(form: SourceForm): SourceFormErrors {
  const errors: SourceFormErrors = {};
  if (!form.name.trim()) errors.name = "Name the source, e.g. Sales warehouse.";
  const key = slugError(form.key);
  if (key) errors.key = key;
  else if (form.key === "uploads") errors.key = "“uploads” is the uploaded tables' dataset.";
  const host = form.host.trim();
  if (!host) errors.host = "Enter the database's host name or IP address.";
  else if (host.includes("://") || host.includes("/") || host.includes("@") || !HOST.test(host))
    errors.host = "Only the host name or address: no scheme, user, port or path.";
  if (form.port.trim()) {
    const port = Number(form.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) errors.port = "A port from 1 to 65535.";
  }
  if (!form.database.trim()) errors.database = form.kind === "mysql" ? "Enter the default database." : "Enter the database name.";
  if (!form.username.trim()) errors.username = "Enter the user WorkMind connects as (read-only).";
  if (!form.password) errors.password = "Enter the user's password.";
  const schemas = parseSchemas(form.schemas);
  if (schemas.length === 0) errors.schemas = form.kind === "mysql" ? "List at least one database to read tables from." : "List at least one schema.";
  else if (schemas.length > 20) errors.schemas = "At most 20.";
  else if (schemas.some((s) => !SCHEMA.test(s))) errors.schemas = "Letters, digits, _ and $ only, separated by commas.";
  if (form.tls_ca.trim() && !form.tls_ca.includes("BEGIN CERTIFICATE")) errors.tls_ca = "Paste a PEM certificate (-----BEGIN CERTIFICATE-----).";
  return errors;
}

export function toSourceInput(form: SourceForm): SqlSourceInput {
  return {
    key: form.key,
    name: form.name.trim(),
    kind: form.kind,
    host: form.host.trim(),
    ...(form.port.trim() ? { port: Number(form.port) } : {}),
    database: form.database.trim(),
    username: form.username.trim(),
    password: form.password,
    tls_mode: form.tls_mode,
    tls_ca: form.tls_ca.trim() || null,
    schemas: parseSchemas(form.schemas),
  };
}

/** A key from the name, as the user types it ("Sales warehouse" → "sales-warehouse"). */
export function suggestSourceKey(name: string): string {
  const key = suggestSlug(name).slice(0, 63).replace(/-+$/, "");
  return key === "uploads" ? "uploads-db" : key;
}

export interface SourceStatus {
  tone: "ok" | "error" | "unknown";
  text: string;
}

/** The last connection test or catalog read, in a line. */
export function sourceStatus(source: Pick<SqlSource, "checked_at" | "last_error" | "server_version" | "enabled" | "yaml">): SourceStatus {
  if (!source.enabled) return { tone: "unknown", text: "Turned off: Insights doesn't use it." };
  if (source.last_error) return { tone: "error", text: source.last_error };
  if (!source.checked_at) return { tone: "unknown", text: "Not tested yet." };
  const model = source.yaml ? "" : " No data model yet: people can't ask about it.";
  return { tone: "ok", text: `Connected${source.server_version ? `: ${source.server_version.split(" on ")[0]}` : ""}.${model}` };
}

const quote = (text: string) => JSON.stringify(text);

function modelName(table: CatalogTable, taken: Set<string>): string {
  const base = table.name.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
  let name = /^[a-z_]/.test(base) ? base : `t_${base}`;
  if (taken.has(name)) name = `${table.schema.toLowerCase().replace(/[^a-z0-9_]+/g, "_")}_${name}`;
  return name.slice(0, TABLE_NAME_MAX);
}

/**
 * A starting semantic model for the picked tables of a database source: each table with its
 * source_table and the columns that can be used (lower_snake_case names), its first date column
 * as the time column, open to the whole organization, a row count per table as placeholder
 * metrics. Admins then describe the tables, add joins, real metrics and row policies.
 */
export function scaffoldSourceModel(source: Pick<SqlSource, "name" | "kind">, tables: readonly CatalogTable[]): string {
  const taken = new Set<string>();
  const named = tables.map((table) => {
    const name = modelName(table, taken);
    taken.add(name);
    return { name, table, columns: table.columns.filter((c) => c.usable) };
  });
  const lines = [
    `# A starting point generated from ${KIND_LABELS[source.kind]} tables. Describe each table and`,
    "# column, add joins, the metrics people ask about, and row policies, then save. SQL in the",
    `# model (metrics, filters, dimensions) is ${KIND_LABELS[source.kind]} SQL.`,
    `name: ${quote(source.name)}`,
    "description: What this data covers and where it comes from.",
    "access:",
    "  - org   # everyone in the organization; or department:finance, group:leadership, user:dan@acme.test",
    "tables:",
  ];
  for (const { name, table, columns } of named) {
    const timeColumn = columns.find((c) => c.model_type === "date" || c.model_type === "timestamp");
    lines.push(`  ${name}:`, `    source_table: ${table.schema}.${table.name}`, `    description: ${quote(`TODO: what one row of ${table.name} is`)}`);
    if (timeColumn) lines.push(`    time_column: ${timeColumn.name}`);
    lines.push("    columns:");
    for (const column of columns) lines.push(`      ${column.name}:`, `        type: ${column.model_type}`);
    const skipped = table.columns.filter((c) => !c.usable).map((c) => c.name);
    if (skipped.length > 0) lines.push(`    # left out (not lower_snake_case; rename them in a view): ${skipped.join(", ")}`);
  }
  lines.push("joins: []   # e.g. - {left: orders.customer_id, right: customers.customer_id}", "dimensions: {}", "metrics:");
  for (const { name } of named) {
    lines.push(
      `  ${`${name}_count`.slice(0, TABLE_NAME_MAX)}:`,
      `    table: ${name}`,
      "    sql: count(*)",
      `    description: ${quote(`Number of ${name.replace(/_/g, " ")}`)}`,
      "    format: number",
    );
  }
  lines.push("row_policies: []");
  return `${lines.join("\n")}\n`;
}
