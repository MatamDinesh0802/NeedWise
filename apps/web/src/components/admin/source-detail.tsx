"use client";

import { CircleAlert, CircleCheck, Clock, ExternalLink, FileWarning, History, LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiQuery } from "@/hooks/use-api-query";
import { usePolling } from "@/hooks/use-polling";
import {
  changeCounts,
  runDuration,
  sourcesApi,
  sourcesKeys,
  staleAfterLabel,
  staleReasonText,
  statsSummary,
  triggerLabel,
  type Source,
  type SourceRun,
  type StaleSyncedDocument,
} from "@/lib/admin/sources";
import { formatDate, formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

export type DetailTab = "runs" | "stale";

/** The side panel of one source: its run history and its stale documents. */
export function SourceDetail({ source, tab, onTab }: { source: Source; tab: DetailTab; onTab: (tab: DetailTab) => void }) {
  return (
    <div className="flex flex-col gap-4 p-4">
      <SheetHeader className="p-0">
        <SheetTitle>{source.name}</SheetTitle>
        <SheetDescription>
          Syncs into {source.collection_name}. The newest 100 runs are kept; stale documents are listed with the source&apos;s own threshold (
          {staleAfterLabel(source.stale_after_days).toLowerCase()}).
        </SheetDescription>
      </SheetHeader>
      <Tabs value={tab} onValueChange={(value) => onTab(value as DetailTab)} className="flex flex-col gap-3">
        <TabsList>
          <TabsTrigger value="runs">Run history</TabsTrigger>
          <TabsTrigger value="stale">
            Stale documents
            {source.stale.not_updated + source.stale.missing_upstream > 0 ? ` (${source.stale.not_updated + source.stale.missing_upstream})` : ""}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="runs">
          <SourceRuns source={source} />
        </TabsContent>
        <TabsContent value="stale">
          <StaleForSource source={source} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function SourceRuns({ source }: { source: Source }) {
  const runs = useApiQuery(sourcesKeys.runs(source.key), () => sourcesApi.runs(source.key, 50));
  const data = runs.state.status === "success" ? runs.state.data : [];
  usePolling(sourcesKeys.runs(source.key), source.syncing || data.some((r) => r.status === "running"), 2500);
  if (runs.state.status === "loading") return <TableSkeleton rows={3} />;
  if (runs.state.status === "error") return <ErrorAlert error={runs.state.error} onRetry={() => void runs.refetch()} />;
  return (
    <div className="flex flex-col gap-2">
      <RefreshingHint active={runs.state.refreshing} />
      <RunList runs={data} />
    </div>
  );
}

const RUN_STYLE: Record<SourceRun["status"], string> = {
  running: "bg-sky-500/15 text-sky-800 dark:text-sky-300",
  ok: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
  error: "bg-red-500/10 text-red-800 dark:text-red-200",
};
const RUN_LABEL: Record<SourceRun["status"], string> = { running: "Running", ok: "Succeeded", error: "Failed" };

/** Newest first: when, why, what changed, and every error with its plain explanation. */
export function RunList({ runs, now }: { runs: readonly SourceRun[]; now?: Date }) {
  if (runs.length === 0) {
    return <EmptyState icon={History} title="No runs yet" description="Choose Sync now, or wait for the schedule." />;
  }
  return (
    <ol className="flex flex-col gap-3" aria-label="Runs, newest first">
      {runs.map((run) => (
        <RunItem key={run.id} run={run} now={now} />
      ))}
    </ol>
  );
}

function RunItem({ run, now }: { run: SourceRun; now?: Date }) {
  const counts = changeCounts(run.stats);
  const duration = runDuration(run);
  const summary = statsSummary(run.stats);
  return (
    <li className="flex flex-col gap-2 rounded-lg border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary" className={RUN_STYLE[run.status]}>
          {run.status === "running" ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : null}
          {run.status === "ok" ? <CircleCheck data-icon="inline-start" aria-hidden="true" /> : null}
          {run.status === "error" ? <CircleAlert data-icon="inline-start" aria-hidden="true" /> : null}
          {RUN_LABEL[run.status]}
        </Badge>
        <time dateTime={run.started_at} title={formatDateTime(run.started_at)} className="font-medium">
          {formatRelative(run.started_at, now)}
        </time>
        <span className="text-xs text-muted-foreground">
          {triggerLabel(run)}
          {duration ? ` · took ${duration}` : ""}
        </span>
      </div>
      {run.status !== "running" ? (
        <dl className="grid grid-cols-3 gap-2 text-xs sm:max-w-sm">
          <div>
            <dt className="text-muted-foreground">Added</dt>
            <dd className="font-medium tabular-nums">{counts.added}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Updated</dt>
            <dd className="font-medium tabular-nums">{counts.updated}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Deleted</dt>
            <dd className="font-medium tabular-nums">{counts.deleted}</dd>
          </div>
        </dl>
      ) : null}
      {summary && run.status === "ok" ? <p className="text-xs text-muted-foreground">{summary}</p> : null}
      {run.complete === false && run.status === "ok" ? (
        <p className="text-xs text-muted-foreground">
          The scan didn&apos;t list everything (a page limit, a truncated tree, or a history window), so nothing was deleted.
        </p>
      ) : null}
      {run.errors.length > 0 ? <RunErrors errors={run.errors} /> : null}
    </li>
  );
}

function RunErrors({ errors }: { errors: SourceRun["errors"] }) {
  return (
    <ul className="flex flex-col gap-2" aria-label="Errors">
      {errors.map((error, index) => (
        <li key={index} className="rounded-md bg-red-500/10 px-3 py-2 text-xs text-red-800 dark:text-red-200">
          <p className="font-medium">
            {error.item ? <span className="mr-1 font-mono break-all">{error.item}:</span> : null}
            {error.plain}
          </p>
          <details className="mt-1">
            <summary className="cursor-pointer select-none">What the source said</summary>
            <p className="mt-1 font-mono break-words">{error.message}</p>
          </details>
        </li>
      ))}
    </ul>
  );
}

function StaleForSource({ source }: { source: Source }) {
  const stale = useApiQuery(sourcesKeys.staleDocuments(source.key, null), () => sourcesApi.staleDocuments({ source: source.key }));
  if (stale.state.status === "loading") return <TableSkeleton rows={3} />;
  if (stale.state.status === "error") return <ErrorAlert error={stale.state.error} onRetry={() => void stale.refetch()} />;
  return (
    <StaleList
      items={stale.state.data}
      showSource={false}
      empty={
        source.stale_after_days === null
          ? "Nothing is missing upstream. This source has no staleness threshold, so unchanged documents aren't listed."
          : `Nothing is missing upstream, and nothing has been unchanged for ${source.stale_after_days} days.`
      }
    />
  );
}

const STALE_WINDOWS = [
  { value: "own", label: "Each source's own threshold" },
  { value: "90", label: "Unchanged for 3 months" },
  { value: "180", label: "Unchanged for 6 months" },
  { value: "365", label: "Unchanged for a year" },
];

/** Across every source on the page (yours, or all for admins). */
export function StaleDocumentsPanel() {
  const [range, setRange] = useState("own");
  const days = range === "own" ? null : Number(range);
  const stale = useApiQuery(sourcesKeys.staleDocuments(null, days), () => sourcesApi.staleDocuments({ days }));
  const items = stale.state.status === "success" ? stale.state.data : [];
  return (
    <section className="flex flex-col gap-3 rounded-xl border p-4" aria-labelledby="stale-synced-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 id="stale-synced-heading" className="flex items-center gap-2 font-medium">
            Stale synced documents
            {stale.state.status === "success" ? <span className="text-sm font-normal text-muted-foreground">({items.length})</span> : null}
            <RefreshingHint active={stale.state.status === "success" && stale.state.refreshing} />
          </h4>
          <p className="text-xs text-muted-foreground">
            Gone from the source (a scan that was cut short no longer lists them: kept until a complete scan decides), or unchanged there for a
            long time. Their owners are reminded once a day, once per document.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="stale-range" className="text-xs text-muted-foreground">
            Stale means
          </label>
          <SimpleSelect id="stale-range" size="sm" value={range} onChange={setRange} options={STALE_WINDOWS} />
        </div>
      </div>
      {stale.state.status === "loading" ? (
        <TableSkeleton rows={2} />
      ) : stale.state.status === "error" ? (
        <ErrorAlert error={stale.state.error} onRetry={() => void stale.refetch()} />
      ) : (
        <StaleList items={items} showSource empty="Nothing looks stale." />
      )}
    </section>
  );
}

/** Stale documents with links: the document in NeedWise, and the original in the source. */
export function StaleList({ items, showSource, empty }: { items: readonly StaleSyncedDocument[]; showSource: boolean; empty: string }) {
  if (items.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <ul className="flex max-h-[28rem] flex-col divide-y overflow-y-auto" aria-label="Stale documents">
      {items.map((doc) => (
        <li key={doc.id} className="flex flex-col gap-1 py-2 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            {doc.title !== null ? (
              <Link href={`/knowledge/documents/${encodeURIComponent(doc.id)}`} className="min-w-0 truncate font-medium underline-offset-2 hover:underline">
                {doc.title}
              </Link>
            ) : (
              <span className="text-muted-foreground">A document in “{doc.collection_name}” you can&apos;t open</span>
            )}
            <Badge
              variant="secondary"
              className={cn(doc.reason === "missing_upstream" ? "bg-amber-500/15 text-amber-900 dark:text-amber-200" : "")}
            >
              {doc.reason === "missing_upstream" ? <FileWarning data-icon="inline-start" aria-hidden="true" /> : <Clock data-icon="inline-start" aria-hidden="true" />}
              {doc.reason === "missing_upstream" ? "Missing upstream" : "Not updated"}
            </Badge>
          </div>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span>{staleReasonText(doc, formatDate)}</span>
            {showSource ? <span>· {doc.source_name}</span> : null}
            <span>· {doc.collection_name}</span>
            <span>· {doc.owner ? `Owner: ${doc.owner.display_name}` : "No owner"}</span>
            {doc.reminded_at ? <span>· reminded {formatDate(doc.reminded_at)}</span> : null}
            {doc.url ? (
              <a href={doc.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 underline-offset-2 hover:underline">
                Open in the source
                <ExternalLink className="size-3" aria-hidden="true" />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            ) : null}
          </p>
        </li>
      ))}
    </ul>
  );
}
