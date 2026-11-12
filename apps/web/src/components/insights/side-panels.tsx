"use client";

import { ChevronRight, History, ShieldCheck } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { EmptyState, ErrorAlert, RefreshingHint } from "@/components/data/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-api-query";
import { analyticsApi, analyticsKeys, type AnalyticsModelAvailable, type RunSummary } from "@/lib/analytics/api";
import { verifiedLabel } from "@/lib/analytics/ask";
import { formatDay } from "@/lib/analytics/format";
import { formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

import { StatusBadge } from "./answer-view";

const GRAINS = new Set(["day", "week", "month", "quarter", "year"]);

/** What the data covers, what you may see, and what you can ask about. */
export function ModelSummary({ model }: { model: AnalyticsModelAvailable }) {
  const grains = model.dimensions.filter((d) => GRAINS.has(d.name));
  const dimensions = model.dimensions.filter((d) => !GRAINS.has(d.name));
  return (
    <section aria-labelledby="insights-model" className="flex flex-col gap-3 rounded-xl border bg-card p-4 text-sm">
      <div>
        <h3 id="insights-model" className="font-medium">
          {model.name}
        </h3>
        {model.description ? <p className="text-xs text-muted-foreground">{model.description}</p> : null}
        {model.data_through ? <p className="mt-1 text-xs text-muted-foreground">Data through {formatDay(model.data_through)}</p> : null}
      </div>
      {model.row_policy ? (
        <p className="flex items-start gap-1.5 rounded-lg bg-muted/50 px-2.5 py-2 text-xs">
          <ShieldCheck className="mt-px size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span>
            <span className="font-medium">Your access: </span>
            {model.row_policy}
          </span>
        </p>
      ) : null}
      <Disclosure title={`Metrics (${model.metrics.length})`}>
        <ul className="flex flex-col gap-1.5">
          {model.metrics.map((metric) => (
            <li key={metric.name}>
              <span className="font-mono text-xs">{metric.name}</span>
              {metric.description ? <span className="block text-xs text-muted-foreground">{metric.description}</span> : null}
            </li>
          ))}
        </ul>
      </Disclosure>
      {dimensions.length > 0 || grains.length > 0 ? (
        <Disclosure title={`Breakdowns (${dimensions.length + grains.length})`}>
          <ul className="flex flex-col gap-1.5">
            {dimensions.map((dimension) => (
              <li key={dimension.name}>
                <span className="font-mono text-xs">{dimension.name}</span>
                {dimension.description ? <span className="block text-xs text-muted-foreground">{dimension.description}</span> : null}
              </li>
            ))}
            {grains.length > 0 ? (
              <li className="text-xs text-muted-foreground">
                Over time by <span className="font-mono text-foreground">{grains.map((g) => g.name).join(", ")}</span>
              </li>
            ) : null}
          </ul>
        </Disclosure>
      ) : null}
      {model.tables.length > 0 ? (
        <Disclosure title={`Tables (${model.tables.length})`}>
          <ul className="flex flex-col gap-2">
            {model.tables.map((table) => (
              <li key={table.name}>
                <span className="font-mono text-xs">{table.name}</span>
                {table.description ? <span className="block text-xs text-muted-foreground">{table.description}</span> : null}
                <span className="mt-0.5 block font-mono text-[11px] break-words text-muted-foreground">
                  {table.columns.map((c) => c.name).join(", ")}
                </span>
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
    </section>
  );
}

function Disclosure({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="group rounded-lg border px-2.5 py-1.5 [&[open]]:pb-2.5">
      <summary className="flex cursor-pointer list-none items-center gap-1 text-xs font-medium focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-3.5 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
        {title}
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

/** The caller's saved answers, newest first (only their own). */
export function HistoryList({ activeId, onOpen }: { activeId: string | null; onOpen: () => void }) {
  const runs = useApiQuery(analyticsKeys.runs, analyticsApi.runs);
  return (
    <section aria-labelledby="insights-history" className="flex flex-col gap-2 rounded-xl border bg-card p-4">
      <h3 id="insights-history" className="flex items-center gap-2 text-sm font-medium">
        <History className="size-4 text-muted-foreground" aria-hidden="true" />
        Your answers
        <RefreshingHint active={runs.state.status === "success" && runs.state.refreshing} />
      </h3>
      {runs.state.status === "loading" ? (
        <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading your answers">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : runs.state.status === "error" ? (
        <ErrorAlert error={runs.state.error} onRetry={() => void runs.refetch()} />
      ) : runs.state.data.length === 0 ? (
        <EmptyState icon={History} title="No answers yet" description="Answers you get are saved here. Only you can see them." />
      ) : (
        <ol className="-mx-1 flex max-h-[28rem] flex-col gap-0.5 overflow-y-auto">
          {runs.state.data.map((run) => (
            <HistoryItem key={run.id} run={run} active={run.id === activeId} onOpen={onOpen} />
          ))}
        </ol>
      )}
    </section>
  );
}

function HistoryItem({ run, active, onOpen }: { run: RunSummary; active: boolean; onOpen: () => void }) {
  const checked = verifiedLabel(run.verified, run.total);
  return (
    <li>
      <Link
        href={`/insights?run=${encodeURIComponent(run.id)}`}
        scroll={false}
        aria-current={active ? "page" : undefined}
        onClick={onOpen}
        className={cn(
          "flex flex-col gap-1 rounded-lg px-2 py-1.5 text-sm hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
          active && "bg-muted",
        )}
      >
        <span className="line-clamp-2">{run.question}</span>
        <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
          {run.status !== "answered" ? <StatusBadge status={run.status} /> : null}
          {checked ? <span className={cn(run.verified < run.total && "text-amber-800 dark:text-amber-300")}>{checked}</span> : null}
          <span title={formatDateTime(run.created_at)}>{formatRelative(run.created_at)}</span>
        </span>
      </Link>
    </li>
  );
}
