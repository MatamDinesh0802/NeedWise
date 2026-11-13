/**
 * The live steps of an Insights answer (POST /analytics/runs, then its SSE stream): a pure
 * reducer over the stream's events, and the step list the page shows. Framework-free and
 * unit-tested; `progress-stream.ts` feeds it from an EventSource.
 *
 * Events: run.started; analytics.step with `step` planning, query_compiled, executing,
 * query_done, query_failed, composing, verifying, repairing, charting; then run.completed
 * (`status`: answered, no_access or failed) or run.failed (`code`, `message`).
 */

import { compareEventIds } from "@/lib/chat/run-reducer";

import type { AnswerStatus, SqlDialect } from "./api";

export const PROGRESS_EVENT_TYPES = ["run.started", "analytics.step", "run.completed", "run.failed"] as const;
export type ProgressEventType = (typeof PROGRESS_EVENT_TYPES)[number];

export interface ProgressEvent {
  id: string | null;
  type: string;
  data: unknown;
}

export interface QueryProgress {
  /** 1, 2, … in the order the agent tried them (failed attempts included). */
  attempt: number;
  tool: string;
  purpose: string;
  dataset: string | null;
  dialect: SqlDialect | null;
  /** The SQL as checked (before row policies), once compiled. */
  sql: string | null;
  status: "running" | "done" | "failed";
  /** "Q1" once it returned rows. */
  queryId: string | null;
  rows: number | null;
  truncated: boolean;
  elapsedMs: number | null;
  error: string | null;
}

export type ProgressPhase = "connecting" | "planning" | "querying" | "composing" | "verifying" | "charting" | "completed" | "failed";

export interface ProgressState {
  phase: ProgressPhase;
  lastEventId: string | null;
  datasets: string[];
  queries: QueryProgress[];
  verification: { verified: number; total: number } | null;
  repaired: string[] | null;
  /** The chart picked: undefined until charting, null when nothing is plotted. */
  chartType: string | null | undefined;
  /** run.completed's status. */
  status: AnswerStatus | null;
  error: { code: string; message: string } | null;
}

export function initialProgress(): ProgressState {
  return {
    phase: "connecting",
    lastEventId: null,
    datasets: [],
    queries: [],
    verification: null,
    repaired: null,
    chartType: undefined,
    status: null,
    error: null,
  };
}

export function isProgressDone(state: ProgressState): boolean {
  return state.phase === "completed" || state.phase === "failed";
}

function record(data: unknown): Record<string, unknown> {
  return typeof data === "object" && data !== null && !Array.isArray(data) ? (data as Record<string, unknown>) : {};
}

const str = (value: unknown): string | null => (typeof value === "string" ? value : null);
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const DIALECTS: readonly SqlDialect[] = ["duckdb", "postgres", "mysql"];
const STATUSES: readonly AnswerStatus[] = ["answered", "no_access", "failed"];

/** The query being run: the last one, while it runs. */
function updateRunning(queries: QueryProgress[], patch: Partial<QueryProgress>): QueryProgress[] {
  const last = queries.at(-1);
  if (!last || last.status !== "running") return queries;
  return [...queries.slice(0, -1), { ...last, ...patch }];
}

function step(state: ProgressState, data: Record<string, unknown>): ProgressState {
  switch (data.step) {
    case "planning":
      return {
        ...state,
        phase: "planning",
        datasets: Array.isArray(data.datasets) ? data.datasets.filter((d): d is string => typeof d === "string") : state.datasets,
      };
    case "query_compiled": {
      const dialect = DIALECTS.find((d) => d === data.dialect) ?? null;
      const query: QueryProgress = {
        attempt: state.queries.length + 1,
        tool: str(data.tool) ?? "",
        purpose: str(data.purpose) ?? "",
        dataset: str(data.dataset),
        dialect,
        sql: str(data.sql),
        status: "running",
        queryId: null,
        rows: null,
        truncated: false,
        elapsedMs: null,
        error: null,
      };
      return { ...state, phase: "querying", queries: [...state.queries, query] };
    }
    case "executing":
      return { ...state, phase: "querying" };
    case "query_done":
      return {
        ...state,
        queries: updateRunning(state.queries, {
          status: "done",
          queryId: str(data.query),
          rows: num(data.rows),
          truncated: data.truncated === true,
          elapsedMs: num(data.elapsed_ms),
        }),
      };
    case "query_failed": {
      const error = str(data.error) ?? "The query failed.";
      const running = state.queries.at(-1)?.status === "running";
      // A tool call refused before compiling (bad input, unknown dataset) has no row yet.
      const queries = running
        ? updateRunning(state.queries, { status: "failed", error })
        : [
            ...state.queries,
            {
              attempt: state.queries.length + 1,
              tool: str(data.tool) ?? "",
              purpose: str(data.purpose) ?? "",
              dataset: str(data.dataset),
              dialect: null,
              sql: null,
              status: "failed" as const,
              queryId: null,
              rows: null,
              truncated: false,
              elapsedMs: null,
              error,
            },
          ];
      return { ...state, queries };
    }
    case "composing":
      return { ...state, phase: "composing" };
    case "verifying":
      return { ...state, phase: "verifying", verification: { verified: num(data.verified) ?? 0, total: num(data.total) ?? 0 } };
    case "repairing":
      return {
        ...state,
        phase: "composing",
        repaired: Array.isArray(data.unverified) ? data.unverified.filter((d): d is string => typeof d === "string") : [],
      };
    case "charting":
      return { ...state, phase: "charting", chartType: str(data.type) };
    default:
      return state;
  }
}

export function reduceProgress(state: ProgressState, event: ProgressEvent): ProgressState {
  // Replays (a reconnect sends Last-Event-ID) and anything after the end are ignored.
  if (event.id && state.lastEventId && compareEventIds(event.id, state.lastEventId) <= 0) return state;
  if (isProgressDone(state)) return state;
  const next = { ...state, lastEventId: event.id ?? state.lastEventId };
  const data = record(event.data);
  switch (event.type) {
    case "run.started":
      return { ...next, phase: next.phase === "connecting" ? "planning" : next.phase };
    case "analytics.step":
      return step(next, data);
    case "run.completed":
      return { ...next, phase: "completed", status: STATUSES.find((s) => s === data.status) ?? "answered" };
    case "run.failed":
      return {
        ...next,
        phase: "failed",
        error: { code: str(data.code) ?? "failed", message: str(data.message) ?? "Something went wrong while answering." },
      };
    default:
      return next;
  }
}

// --- What the page shows ------------------------------------------------------------------

export type StepStatus = "pending" | "active" | "done" | "failed";

export interface ProgressStep {
  key: string;
  label: string;
  status: StepStatus;
  detail: string | null;
  /** The SQL of a query step, as checked. */
  sql?: string | null;
}

const ORDER: ProgressPhase[] = ["connecting", "planning", "querying", "composing", "verifying", "charting", "completed"];

function reached(state: ProgressState, phase: ProgressPhase): boolean {
  if (state.phase === "failed") return false;
  return ORDER.indexOf(state.phase) >= ORDER.indexOf(phase);
}

const DIALECT_NAMES: Record<SqlDialect, string> = { duckdb: "DuckDB", postgres: "PostgreSQL", mysql: "MySQL" };

function queryLabel(query: QueryProgress): string {
  const name = query.queryId ?? `Query ${query.attempt}`;
  return query.purpose ? `${name}: ${query.purpose}` : name;
}

function queryDetail(query: QueryProgress, several: boolean): string {
  const where = query.dataset && several ? `${query.dataset}${query.dialect ? ` (${DIALECT_NAMES[query.dialect]})` : ""} · ` : "";
  if (query.status === "failed") {
    // The agent's reason starts with the API's code ("query_failed: …"): people get the words.
    const reason = (query.error ?? "Failed").replace(/^[a-z_]+: /, "").replace(/\.$/, "");
    return `${where}${reason}. The assistant can correct it and try again.`;
  }
  if (query.status === "running") return `${where}Running with your access…`;
  const rows = query.rows ?? 0;
  const count = `${rows.toLocaleString("en")} ${rows === 1 ? "row" : "rows"}${query.truncated ? " (more matched)" : ""}`;
  return `${where}${count}${query.elapsedMs != null ? ` in ${query.elapsedMs.toLocaleString("en")} ms` : ""}`;
}

/** The steps in order: planning, one per query tried, writing, checking figures, the chart. */
export function progressSteps(state: ProgressState): ProgressStep[] {
  const failed = state.phase === "failed";
  const several = state.datasets.length > 1 || state.queries.some((q) => q.dataset && q.dataset !== "uploads");
  const steps: ProgressStep[] = [
    {
      key: "planning",
      label: "Planning the queries",
      status: state.queries.length > 0 || reached(state, "composing") ? "done" : failed ? "failed" : "active",
      detail: several && state.datasets.length > 0 ? `Datasets: ${state.datasets.join(", ")}` : null,
    },
  ];
  for (const query of state.queries) {
    steps.push({
      key: `query-${query.attempt}`,
      label: queryLabel(query),
      status: query.status === "done" ? "done" : query.status === "failed" ? "failed" : failed ? "failed" : "active",
      detail: queryDetail(query, several),
      sql: query.sql,
    });
  }
  const composing = state.phase === "composing";
  steps.push({
    key: "composing",
    label: state.repaired ? "Rewriting figures that didn't match" : "Writing the answer",
    status: reached(state, "verifying") ? "done" : composing ? "active" : failed && state.queries.length > 0 ? "failed" : "pending",
    detail: state.repaired && state.repaired.length > 0 ? `Not in the results: ${state.repaired.join(", ")}` : null,
  });
  const verification = state.verification;
  steps.push({
    key: "verifying",
    label: "Checking every figure against the results",
    status: reached(state, "charting") ? "done" : state.phase === "verifying" ? "active" : "pending",
    detail: verification ? (verification.total === 0 ? "No figures to check" : `${verification.verified} of ${verification.total} backed by a result`) : null,
  });
  steps.push({
    key: "charting",
    label: "Choosing the chart",
    status: reached(state, "completed") ? "done" : state.phase === "charting" ? "active" : "pending",
    detail: state.chartType === undefined ? null : state.chartType ? `${state.chartType.replace("_", " ")} chart` : "No chart for this answer",
  });
  return steps;
}

/** One line for the screen-reader live region: what is happening now. */
export function progressAnnouncement(state: ProgressState): string {
  switch (state.phase) {
    case "connecting":
    case "planning":
      return "Planning the queries.";
    case "querying": {
      const query = state.queries.at(-1);
      return query ? `${queryLabel(query)}: ${query.status === "running" ? "running" : query.status}.` : "Running queries.";
    }
    case "composing":
      return "Writing the answer.";
    case "verifying":
      return "Checking the figures.";
    case "charting":
      return "Choosing the chart.";
    case "completed":
      return "The answer is ready.";
    case "failed":
      return state.error?.message ?? "The answer failed.";
  }
}
