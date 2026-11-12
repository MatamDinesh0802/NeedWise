/**
 * Typed client for the analytics (BI) API: /api/v1/analytics and /api/v1/admin/analytics.
 * Shapes follow the Phase 10 analytics contract (apps/api/src/workmind/api/v1/analytics.py).
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
  /** 403 "no_access"; 422 "sql_rejected" with a detail saying why. */
  query: (sql: string) => apiJson<QueryRerun>(`${BASE}/query`, { method: "POST", json: { sql } }),

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
};

/** Query-cache keys ("insights/", not "analytics/": Phase 11 usage analytics is a different page). */
export const analyticsKeys = {
  all: "insights/",
  model: "insights/model",
  runs: "insights/runs",
  run: (runId: string) => `insights/runs/${runId}`,
  admin: "insights/admin",
};
