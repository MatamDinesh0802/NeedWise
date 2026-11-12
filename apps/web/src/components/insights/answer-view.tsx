"use client";

import { CircleAlert, Database, Info, LockKeyhole, MessageSquareText, ShieldCheck, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AnalyticsAnswer, AnswerStatus, Verification } from "@/lib/analytics/api";
import { failureAdvice, STATUS_LABELS } from "@/lib/analytics/ask";
import { shapeChart } from "@/lib/analytics/chart-data";
import { formatCost, formatCount } from "@/lib/analytics/format";
import { UNVERIFIED_EXPLANATION, verificationSummary } from "@/lib/analytics/narrative";
import { formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

import { AnalyticsChart } from "./analytics-chart";
import { Narrative } from "./narrative";
import { QueryPanel, type Refreshed } from "./query-panel";

const STATUS_STYLE: Record<AnswerStatus, string> = {
  answered: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
  no_access: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  failed: "bg-destructive/10 text-destructive",
};

export function StatusBadge({ status }: { status: AnswerStatus }) {
  return (
    <Badge variant="secondary" className={STATUS_STYLE[status] ?? ""}>
      {STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

/**
 * A saved answer: chart, narrative with checked figures, and every query behind it.
 * `focusOnShow` moves keyboard focus to the answer's heading (after asking or opening one).
 */
export function AnswerView({
  answer,
  focusOnShow = false,
  onAskAgain,
}: {
  answer: AnalyticsAnswer;
  focusOnShow?: boolean;
  onAskAgain?: (question: string) => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [refreshed, setRefreshed] = useState<Record<string, Refreshed>>({});

  useEffect(() => {
    if (focusOnShow) headingRef.current?.focus();
  }, [focusOnShow, answer.id]);

  const headingId = `answer-${answer.id}-heading`;
  const queryIds = answer.queries.map((q) => q.id);
  const mainId = answer.main_query ?? answer.chart?.query ?? null;
  const chartQuery = answer.chart ? (refreshed[answer.chart.query] ?? answer.queries.find((q) => q.id === answer.chart!.query)) : undefined;
  const shaped = answer.chart && chartQuery ? shapeChart(answer.chart, chartQuery) : null;
  const chartRefreshed = answer.chart ? refreshed[answer.chart.query] !== undefined : false;
  const advice = answer.status === "failed" ? failureAdvice(answer.error) : null;

  return (
    <article aria-labelledby={headingId} className="flex min-w-0 flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h3 id={headingId} ref={headingRef} tabIndex={-1} className="text-lg font-semibold tracking-tight outline-none focus-visible:ring-2 focus-visible:ring-ring/50">
            {answer.question}
          </h3>
          <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <StatusBadge status={answer.status} />
            <span title={formatDateTime(answer.created_at)}>Asked {formatRelative(answer.created_at)}</span>
          </p>
        </div>
        {onAskAgain ? (
          <Button variant="outline" size="sm" onClick={() => onAskAgain(answer.question)}>
            <MessageSquareText data-icon="inline-start" />
            Ask again
          </Button>
        ) : null}
      </header>

      {answer.status === "no_access" ? (
        <Alert>
          <LockKeyhole aria-hidden="true" />
          <AlertTitle>You don&apos;t have access to the data this needs</AlertTitle>
          <AlertDescription>
            <p>{answer.error ?? "The data model doesn't let you query this."}</p>
            <p>Nothing was queried on your behalf. Ask an administrator if you need this data.</p>
          </AlertDescription>
        </Alert>
      ) : null}
      {answer.status === "failed" ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>This question couldn&apos;t be answered</AlertTitle>
          <AlertDescription>
            <p>{answer.error ?? "Something went wrong while answering."}</p>
            {advice ? <p>{advice}</p> : null}
          </AlertDescription>
        </Alert>
      ) : null}

      {shaped?.kind === "chart" ? (
        <div className="rounded-xl border bg-card p-4">
          <AnalyticsChart data={shaped.data} />
          {chartQuery?.truncated ? (
            <p className="mt-3 flex items-start gap-1.5 text-xs text-amber-900 dark:text-amber-200">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              The chart shows only the first {formatCount(chartQuery.rows.length)} rows of {answer.chart?.query}: the query had more than the row limit, so the
              chart is incomplete.
            </p>
          ) : null}
          {chartRefreshed ? (
            <p className="mt-3 flex items-start gap-1.5 text-xs text-muted-foreground">
              <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              The chart shows the refreshed results. The text and its checks describe the results when the answer was made.
            </p>
          ) : null}
        </div>
      ) : shaped?.kind === "unavailable" ? (
        <p className="flex items-start gap-1.5 rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          No chart: {shaped.reason}
        </p>
      ) : null}

      {answer.narrative.trim() ? (
        <div className="flex flex-col gap-3 rounded-xl border bg-card p-4">
          <Narrative text={answer.narrative} numbers={answer.verification.numbers} answerId={answer.id} queryIds={queryIds} />
          <VerificationNote verification={answer.verification} />
        </div>
      ) : null}

      {answer.queries.length > 0 ? (
        <section aria-labelledby={`${headingId}-queries`} className="flex flex-col gap-3">
          <h4 id={`${headingId}-queries`} className="text-sm font-medium">
            {answer.queries.length === 1 ? "The query behind this answer" : `The ${answer.queries.length} queries behind this answer`}
          </h4>
          {answer.queries.map((query) => (
            <QueryPanel
              key={query.id}
              answerId={answer.id}
              question={answer.question}
              query={query}
              main={query.id === mainId}
              refreshed={refreshed[query.id]}
              onRefreshed={(id, result) => setRefreshed((current) => ({ ...current, [id]: result }))}
            />
          ))}
        </section>
      ) : null}

      <footer className="flex flex-col gap-3 border-t pt-3 text-xs text-muted-foreground sm:flex-row sm:items-start sm:justify-between">
        {answer.sources.length > 0 ? (
          <div className="flex min-w-0 flex-col gap-1">
            <p className="flex items-center gap-1.5 font-medium text-foreground">
              <Database className="size-3.5" aria-hidden="true" />
              Data used
            </p>
            <ul className="flex flex-col gap-0.5">
              {answer.sources.map((source) => (
                <li key={source.table}>
                  <span className="font-mono text-foreground">{source.table}</span>
                  {source.description ? ` · ${source.description}` : ""}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <span />
        )}
        <p className="shrink-0 tabular-nums" title="Model, tokens and cost of this answer">
          {answer.usage.model} · {formatCount(answer.usage.input_tokens)} in / {formatCount(answer.usage.output_tokens)} out tokens ·{" "}
          {formatCost(answer.usage.cost_usd)}
        </p>
      </footer>
    </article>
  );
}

/** How many figures in the text were found in the query results, and what to do about the rest. */
export function VerificationNote({ verification }: { verification: Verification }) {
  const unverified = verification.numbers.filter((n) => !n.verified);
  const allGood = verification.total > 0 && unverified.length === 0;
  const Icon = unverified.length > 0 ? TriangleAlert : ShieldCheck;
  return (
    <div
      className={cn(
        "flex flex-col gap-1.5 rounded-lg px-3 py-2 text-xs",
        unverified.length > 0 ? "bg-amber-500/10 text-amber-950 dark:text-amber-100" : "bg-muted/50 text-muted-foreground",
      )}
      data-verified={allGood ? "all" : unverified.length > 0 ? "some" : "none"}
    >
      <p className="flex items-start gap-1.5">
        <Icon className={cn("mt-px size-3.5 shrink-0", allGood && "text-emerald-700 dark:text-emerald-400")} aria-hidden="true" />
        <span>
          {verificationSummary(verification.verified, verification.total)}
          {allGood ? " Each is underlined; hover to see which query it comes from." : ""}
        </span>
      </p>
      {unverified.length > 0 ? (
        <>
          <ul className="flex flex-wrap gap-1.5 pl-5">
            {unverified.map((n, i) => (
              <li key={`${n.text}-${i}`} className="rounded-sm bg-amber-500/15 px-1 font-medium ring-1 ring-amber-500/40">
                {n.text}
              </li>
            ))}
          </ul>
          <p className="pl-5">{UNVERIFIED_EXPLANATION}</p>
        </>
      ) : null}
    </div>
  );
}
