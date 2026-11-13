"use client";

import { Check, ChevronRight, CircleAlert, Copy, Download, LoaderCircle, RefreshCw, Star } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { analyticsApi, UPLOADS, type QueryRerun, type QueryResult, type SqlDialect } from "@/lib/analytics/api";
import { csvFilename, downloadText, toCsv } from "@/lib/analytics/csv";
import { formatCount, formatElapsed } from "@/lib/analytics/format";
import { errorMessage } from "@/lib/error-messages";
import { cn } from "@/lib/utils";

import { queryAnchor } from "./narrative";
import { ResultTable } from "./result-table";

export interface Refreshed extends QueryRerun {
  at: string;
}

const DIALECT_NAMES: Record<SqlDialect, string> = { duckdb: "DuckDB", postgres: "PostgreSQL", mysql: "MySQL" };

/** "shop · PostgreSQL" for a query on a database source; null for the uploaded files. */
export function datasetLabel(query: Pick<QueryResult, "dataset" | "dialect">): string | null {
  if (!query.dataset || query.dataset === UPLOADS) return null;
  return query.dialect ? `${query.dataset} · ${DIALECT_NAMES[query.dialect]}` : query.dataset;
}

/** What a failed refresh means, in plain words. */
export function refreshErrorText(error: unknown): { title: string; description: string } {
  if (error instanceof ApiError && error.code === "no_access") {
    return { title: "You no longer have access", description: "Your access to this data changed since the answer was saved." };
  }
  if (error instanceof ApiError && error.code === "sql_rejected") {
    return { title: "The SQL was rejected", description: error.detail ?? "It no longer passes the safety checks (for example, a table was removed)." };
  }
  const message = errorMessage(error);
  return { title: message.title, description: message.description };
}

/**
 * One query of an answer: why it ran, its SQL (copyable), the result table, a CSV download and
 * a refresh that re-runs the SQL with your current access.
 */
export function QueryPanel({
  answerId,
  question,
  query,
  main,
  refreshed,
  onRefreshed,
}: {
  answerId: string;
  question: string;
  query: QueryResult;
  main: boolean;
  refreshed: Refreshed | undefined;
  onRefreshed: (queryId: string, result: Refreshed) => void;
}) {
  const [showSql, setShowSql] = useState(main);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<unknown>(null);
  const data = refreshed ?? query;
  const sqlId = `${queryAnchor(answerId, query.id)}-sql`;

  async function refresh() {
    setRefreshing(true);
    setRefreshError(null);
    try {
      const result = await analyticsApi.query(query.sql, query.dataset);
      onRefreshed(query.id, { ...result, at: new Date().toISOString() });
    } catch (error) {
      setRefreshError(error);
    } finally {
      setRefreshing(false);
    }
  }

  const failure = refreshError ? refreshErrorText(refreshError) : null;
  const where = datasetLabel(query);

  return (
    <section
      id={queryAnchor(answerId, query.id)}
      aria-labelledby={`${queryAnchor(answerId, query.id)}-title`}
      className="flex scroll-mt-20 flex-col gap-3 rounded-xl border bg-card p-4 target:ring-2 target:ring-primary/40"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 id={`${queryAnchor(answerId, query.id)}-title`} className="flex flex-wrap items-center gap-2 font-medium">
            <Badge variant="outline" className="font-mono">
              {query.id}
            </Badge>
            <span className="min-w-0">{query.purpose || "Query"}</span>
            {where ? (
              <Badge variant="outline" className="font-normal" title="The database this query ran on">
                {where}
              </Badge>
            ) : null}
            {main ? (
              <Badge variant="secondary" className="font-normal">
                <Star data-icon="inline-start" aria-hidden="true" />
                Answers the question
              </Badge>
            ) : null}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {query.error ? "Failed" : `${formatCount(data.row_count)} ${data.row_count === 1 ? "row" : "rows"} · ${formatElapsed(data.elapsed_ms)}`}
            {refreshed ? ` · refreshed ${new Date(refreshed.at).toLocaleTimeString()}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button
            variant="outline"
            size="sm"
            disabled={refreshing || !query.sql}
            onClick={() => void refresh()}
            title="Run this SQL again now, with your current access"
          >
            {refreshing ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <RefreshCw data-icon="inline-start" />}
            Refresh
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={data.columns.length === 0}
            onClick={() =>
              downloadText(
                toCsv(
                  data.columns.map((c) => c.name),
                  data.rows,
                ),
                csvFilename(question, query.id),
              )
            }
          >
            <Download data-icon="inline-start" />
            CSV
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
            aria-expanded={showSql}
            aria-controls={sqlId}
            onClick={() => setShowSql((s) => !s)}
          >
            <ChevronRight className={cn("size-3.5 transition-transform", showSql && "rotate-90")} aria-hidden="true" />
            SQL
          </button>
          {showSql ? <CopyButton text={query.sql} /> : null}
        </div>
        {showSql ? (
          <pre id={sqlId} className="max-h-72 overflow-auto rounded-lg border bg-muted/40 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
            {query.sql}
          </pre>
        ) : null}
      </div>

      {query.error ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>This query failed</AlertTitle>
          <AlertDescription>{query.error}</AlertDescription>
        </Alert>
      ) : null}

      {failure ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>{failure.title}</AlertTitle>
          <AlertDescription>{failure.description}</AlertDescription>
        </Alert>
      ) : null}

      {!query.error || refreshed ? <ResultTable columns={data.columns} rows={data.rows} caption={`${query.id}: ${query.purpose}`} /> : null}

      {data.truncated ? (
        <p className="text-xs text-amber-800 dark:text-amber-300">
          Only the first {formatCount(data.rows.length)} rows are shown and in the CSV; more rows matched. Narrow the question (a period, a
          region) to see everything.
        </p>
      ) : null}
    </section>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      size="xs"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
      <span aria-live="polite">{copied ? "Copied" : "Copy SQL"}</span>
    </Button>
  );
}
