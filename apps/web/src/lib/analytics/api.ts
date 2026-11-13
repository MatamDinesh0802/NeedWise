/**
 * Typed client for the analytics (BI) API: /api/v1/analytics and /api/v1/admin/analytics.
 * Shapes follow the Phase 10 analytics contract (apps/api/src/needwise/api/v1/analytics.py).
 * Everything goes through the shared wrapper (CSRF header, problem+json errors as ApiError).
 */

import { apiJson } from "@/lib/api";

// --- Model ----------------------------------------------------------------------------------

/** How a metric or chart axis is formatted. Result columns can also be "text" or "date". */
export type ValueFormat = "eur" | "number" | "percent";
export type ColumnFormat = ValueFormat | "text" | "date";

export interface ModelMetric {
  name: string;
  description: string;
  format: ValueFormat;
}

export interface ModelDimension {
  name: string;
  description: string;
}

export interface ModelColumn {
  name: string;
  type: string;
  description: string;
}

export interface ModelTable {
  name: string;
  description: string;
  columns: ModelColumn[];
}

/** Where a dataset's data lives: uploaded files, or a PostgreSQL or MySQL database. */
export type DatasetKind = "files" | "postgres" | "mysql";
/** The SQL a dataset's queries are written in. */
export type SqlDialect = "duckdb" | "postgres" | "mysql";
/** The key of the uploaded files' dataset. */
export const UPLOADS = "uploads";

/** One dataset the caller may query (GET /analytics/model `datasets`). */
export interface AnalyticsDataset {
  key: string;
  kind: DatasetKind;
  dialect: SqlDialect;
  name: string;
  description: string;
  data_through: string | null;
  metrics: ModelMetric[];
  dimensions: ModelDimension[];
  tables: ModelTable[];
  row_policy: string | null;
  examples: string[];
}

export interface AnalyticsModelAvailable {
  available: true;
  name: string;
  description: string;
  /** Last closed period (YYYY-MM-DD), or null when the model doesn't say. */
  data_through: string | null;
  metrics: ModelMetric[];
  /** Includes the time grains month, quarter and year. */
  dimensions: ModelDimension[];
  tables: ModelTable[];
  /** Plain words on the row limits that apply to the caller, or null when there are none. */
  row_policy: string | null;
  examples: string[];
  /** Every dataset the caller may query; the fields above describe the first. */
  datasets?: AnalyticsDataset[];
}

export type UnavailableReason = "no_access" | "not_configured";

export interface AnalyticsModelUnavailable {
  available: false;
  reason: UnavailableReason;
  message: string;
}

export type AnalyticsModel = AnalyticsModelAvailable | AnalyticsModelUnavailable;

// --- Answers --------------------------------------------------------------------------------

export type AnswerStatus = "answered" | "no_access" | "failed";
export type ChartType = "line" | "bar" | "stacked_bar" | "table";

/**
 * A row value: JSON number, string, boolean or null. Dates arrive as ISO strings. A LIST column
 * (array_agg, list) arrives as an array, a MAP or STRUCT as an object.
 */
export type Cell = number | string | boolean | null | Cell[] | { [key: string]: Cell };

export interface ChartSpec {
  type: ChartType;
  /** The query id ("Q1") whose rows the chart draws. */
  query: string;
  x: string;
  y: string[];
  /** A column whose values become separate series (long format), or null. */
  series: string | null;
  title: string;
  y_format: ValueFormat;
}

export interface ResultColumn {
  name: string;
  type: string;
  format: ColumnFormat;
}

export interface QueryResult {
  id: string;
  purpose: string;
  sql: string;
  columns: ResultColumn[];
  rows: Cell[][];
  row_count: number;
  /** Rows are capped (1000); true when more rows matched. */
  truncated: boolean;
  elapsed_ms: number;
  error: string | null;
  /** The dataset it ran on ("uploads", or a database source's key): refresh it there. */
  dataset?: string;
  dialect?: SqlDialect;
}

export interface VerifiedNumber {
  /** The number as written in the narrative, e.g. "€5.02M". */
  text: string;
  value: number;
  verified: boolean;
  /** The query whose results contain it, when verified. */
  query: string | null;
}

export interface Verification {
  numbers: VerifiedNumber[];
  verified: number;
  total: number;
}

export interface AnswerSource {
  table: string;
  description: string;
  dataset?: string;
}

export interface AnswerUsage {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
}

export interface AnalyticsAnswer {
  id: string;
  question: string;
  status: AnswerStatus;
  error: string | null;
  /** Markdown; refers to queries as [Q1], [Q2]. */
  narrative: string;
  chart: ChartSpec | null;
  queries: QueryResult[];
  /** The query that answers the question (what the chart shows). */
  main_query: string | null;
  verification: Verification;
  sources: AnswerSource[];
  usage: AnswerUsage;
  created_at: string;
}

export interface RunSummary {
  id: string;
  question: string;
  status: AnswerStatus;
  verified: number;
  total: number;
  created_at: string;
}

/** POST /analytics/query: the same SQL re-run through the guard and the caller's row policies. */
export interface QueryRerun {
  columns: ResultColumn[];
  rows: Cell[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
}

export const QUESTION_MAX_LENGTH = 1000;

// --- Administration -------------------------------------------------------------------------

export interface DatasetColumn {
  name: string;
  type: string;
}

export interface DatasetTable {
  name: string;
  rows: number;
  columns: DatasetColumn[];
}

export interface AdminAnalytics {
  /** The semantic model as saved, or null when none is set up. */
  yaml: string | null;
  dataset: { version: string | null; tables: DatasetTable[] };
}

// --- Database sources (Admin › Analytics) ---------------------------------------------------

export type SourceKind = "postgres" | "mysql";
export type TlsMode = "disable" | "require" | "verify-full";

/** A PostgreSQL or MySQL source as admins see it. The password is never returned. */
export interface SqlSource {
  key: string;
  name: string;
  kind: SourceKind;
  host: string;
  port: number;
  database: string;
  username: string;
  password_set: boolean;
  tls_mode: TlsMode;
  tls_ca: string | null;
  schemas: string[];
  enabled: boolean;
  /** Its semantic model, or null until one is saved (people can't query it before). */
  yaml: string | null;
  checked_at: string | null;
  server_version: string | null;
  /** Why the last connection failed (for admins), or null. */
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface SqlSourceInput {
  key: string;
  name: string;
  kind: SourceKind;
  host: string;
  port?: number;
  database: string;
  username: string;
  password: string;
  tls_mode: TlsMode;
  tls_ca?: string | null;
  schemas: string[];
}

export type SqlSourcePatch = Partial<Omit<SqlSourceInput, "key" | "kind">> & { enabled?: boolean };

export interface SourceCheck {
  ok: boolean;
  server_version: string | null;
  elapsed_ms: number;
  error: string | null;
}

export type ModelColumnType = "text" | "integer" | "decimal" | "date" | "timestamp" | "boolean";

export interface CatalogColumn {
  name: string;
  /** As the database names it (character varying, numeric, int…). */
  type: string;
  model_type: ModelColumnType;
  /** Only lower_snake_case column names can be used in a model. */
  usable: boolean;
}

export interface CatalogTable {
  schema: string;
  name: string;
  kind: "table" | "view";
  columns: CatalogColumn[];
}

export interface SourceCatalog {
  tables: CatalogTable[];
  truncated: boolean;
}

// --- Streamed answers -----------------------------------------------------------------------

/** POST /analytics/runs: the answer is made in the background; follow it at eventsUrl. */
export interface RunStarted {
  run_id: string;
}

export const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/** Content-Type for an upload, from the file name: CSV or XLSX only. */
export function tableContentType(filename: string): string | null {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".csv")) return "text/csv";
  if (lower.endsWith(".xlsx")) return XLSX_TYPE;
  return null;
}

// --- Client ---------------------------------------------------------------------------------

const BASE = "/api/v1/analytics";
const ADMIN = "/api/v1/admin/analytics";
const id = (value: string) => encodeURIComponent(value);

export const analyticsApi = {
  model: () => apiJson<AnalyticsModel>(`${BASE}/model`),
  /** Takes 10–30 s. Resolves with status "no_access" or "failed" too; only transport errors throw. */
  ask: (question: string, signal?: AbortSignal) =>
    apiJson<AnalyticsAnswer>(`${BASE}/ask`, { method: "POST", json: { question }, signal }),
  runs: () => apiJson<RunSummary[]>(`${BASE}/runs`),
  run: (runId: string) => apiJson<AnalyticsAnswer>(`${BASE}/runs/${id(runId)}`),
  /** Answers in the background: 202 with the run's id; its steps stream from eventsUrl. */
  startRun: (question: string) => apiJson<RunStarted>(`${BASE}/runs`, { method: "POST", json: { question } }),
  eventsUrl: (runId: string) => `${BASE}/runs/${id(runId)}/events`,
  /** 403 "no_access"; 422 "sql_rejected" with a detail saying why. `dataset`: where it ran. */
  query: (sql: string, dataset?: string) =>
    apiJson<QueryRerun>(`${BASE}/query`, {
      method: "POST",
      json: dataset && dataset !== UPLOADS ? { sql, dataset } : { sql },
    }),

  // Administration (owner/admin)
  admin: () => apiJson<AdminAnalytics>(ADMIN),
  /** 422 "invalid_model" with a detail listing the problems. */
  saveModel: (yaml: string) => apiJson<AdminAnalytics>(`${ADMIN}/model`, { method: "PUT", json: { yaml } }),
  /** Raw file bytes as the body (not multipart); adds or replaces the table in a new dataset version. */
  uploadTable: (name: string, file: Blob, contentType: string) =>
    apiJson<AdminAnalytics>(`${ADMIN}/tables?name=${encodeURIComponent(name)}`, {
      method: "POST",
      body: file,
      headers: { "Content-Type": contentType },
    }),
  deleteTable: (name: string) => apiJson<AdminAnalytics>(`${ADMIN}/tables/${id(name)}`, { method: "DELETE" }),

  // Database sources (owner/admin)
  sources: () => apiJson<SqlSource[]>(`${ADMIN}/sources`),
  /** 422 "invalid_source" for a host NeedWise may not connect to; 409 for a key in use. */
  createSource: (input: SqlSourceInput) => apiJson<SqlSource>(`${ADMIN}/sources`, { method: "POST", json: input }),
  /** 422 "password_required" when the change points it elsewhere without the password. */
  updateSource: (key: string, patch: SqlSourcePatch) =>
    apiJson<SqlSource>(`${ADMIN}/sources/${id(key)}`, { method: "PATCH", json: patch }),
  deleteSource: (key: string) => apiJson<void>(`${ADMIN}/sources/${id(key)}`, { method: "DELETE" }),
  testSource: (key: string) => apiJson<SourceCheck>(`${ADMIN}/sources/${id(key)}/test`, { method: "POST" }),
  /** 503 "source_unavailable" with the reason when it can't connect. */
  sourceCatalog: (key: string) => apiJson<SourceCatalog>(`${ADMIN}/sources/${id(key)}/catalog`),
  /** 422 "invalid_model" with the problems (also tables or columns the database lacks). */
  saveSourceModel: (key: string, yaml: string) =>
    apiJson<SqlSource>(`${ADMIN}/sources/${id(key)}/model`, { method: "PUT", json: { yaml } }),
};

/** Query-cache keys ("insights/", not "analytics/": Phase 11 usage analytics is a different page). */
export const analyticsKeys = {
  all: "insights/",
  model: "insights/model",
  runs: "insights/runs",
  run: (runId: string) => `insights/runs/${runId}`,
  admin: "insights/admin",
  sources: "insights/admin/sources",
};
