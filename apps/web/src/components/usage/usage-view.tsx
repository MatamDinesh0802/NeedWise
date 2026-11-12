"use client";

import { Activity, Download, Info, LoaderCircle, ReceiptText } from "lucide-react";
import Link from "next/link";
import { useId, useState, type MouseEvent } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { EmptyState, ErrorAlert, TableSkeleton } from "@/components/data/states";
import { EnterpriseBadge, EnterpriseGate, EnterpriseNotice } from "@/components/enterprise/enterprise-gate";
import { AnalyticsChart, ViewToggle } from "@/components/insights/analytics-chart";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { SimpleSelect } from "@/components/data/form";
import { BudgetMeter } from "@/components/usage/budget-meter";
import { useApiQuery } from "@/hooks/use-api-query";
import { useStore } from "@/hooks/use-store";
import { formatNumber } from "@/lib/analytics/format";
import { isEnterpriseFeatureError } from "@/lib/edition";
import { formatDateTime } from "@/lib/format";
import type { Me } from "@/lib/session";
import { createPagedList } from "@/lib/stores/paged-list";
import { cn } from "@/lib/utils";
import {
  chargebackPath,
  usageApi,
  usageKeys,
  type BudgetStatus,
  type GroupBy,
  type Interval,
  type UsageEvent,
  type UsageGroup,
  type UsageScope,
  type UsageSummary,
} from "@/lib/usage/api";
import { breakdown, costChart, visibleBars, type BreakdownRow } from "@/lib/usage/chart";
import {
  intervalFor,
  presetRange,
  RANGE_PRESETS,
  rangeError,
  rangeText,
  type DateRange,
  type RangePreset,
} from "@/lib/usage/dates";
import {
  defaultGroupBy,
  expectedScope,
  featureLabel,
  formatShare,
  formatUsd,
  GROUP_LABELS,
  groupOptions,
  isUsageAdmin,
  kpis,
  scopeHeading,
  sortBudgets,
  unpricedNote,
} from "@/lib/usage/format";

/** /analytics: model usage and cost for whoever is looking, at the scope the API allows them. */
export function UsageView() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <SignedInGate purpose="see model usage and costs">{(me) => <Usage me={me} />}</SignedInGate>
    </div>
  );
}

function managedDepartments(me: Me): UsageScope["departments"] {
  return me.departments.filter((d) => d.role === "manager").map((d) => ({ id: d.id, name: d.name }));
}

function Usage({ me }: { me: Me }) {
  const expected = expectedScope(me);
  const [preset, setPreset] = useState<RangePreset>("this_month");
  const [custom, setCustom] = useState<DateRange>(() => presetRange("this_month"));
  const [groupBy, setGroupBy] = useState<GroupBy>(defaultGroupBy(expected));

  const range = preset === "custom" ? custom : presetRange(preset);
  const problem = preset === "custom" ? rangeError(custom) : null;
  const interval = intervalFor(range);
  const params = problem ? null : { ...range, group_by: groupBy, interval };
  const query = useApiQuery(params ? usageKeys.summary(params) : null, () => usageApi.summary(params!));

  // Refetch keeps the frame: the last answer stays (dimmed) while the next one loads.
  const [shown, setShown] = useState<UsageSummary | null>(null);
  if (query.state.status === "success" && query.state.data !== shown) setShown(query.state.data);

  const scope = shown?.scope ?? { kind: expected, departments: expected === "departments" ? managedDepartments(me) : [] };
  const heading = scopeHeading(scope, me.tenant.name);
  const loading = params !== null && (query.state.status === "loading" || query.state.refreshing);
  // What's on screen doesn't match the filters while the next answer loads, or can't be fetched.
  const stale = loading || problem !== null || query.state.status === "error";

  return (
    <>
      <header className="flex flex-col gap-1">
        <h2 className="text-xl font-semibold tracking-tight">{heading.title}</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {heading.description} Cost is what each model request was priced at; requests and tokens count every request.
        </p>
      </header>

      <UsageFilters
        preset={preset}
        onPreset={(next) => {
          if (next === "custom") setCustom(range);
          setPreset(next);
        }}
        custom={custom}
        onCustom={setCustom}
        problem={problem}
        groupBy={groupBy}
        onGroupBy={setGroupBy}
        groups={groupOptions(expected)}
        range={range}
        interval={interval}
        loading={loading && shown !== null}
      />

      {query.state.status === "error" ? <ErrorAlert error={query.state.error} onRetry={() => void query.refetch()} /> : null}
      {shown ? (
        <UsageReport summary={shown} me={me} stale={stale} />
      ) : query.state.status !== "error" ? (
        <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading usage">
          <div className="grid gap-3 sm:grid-cols-3">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
          <Skeleton className="h-72 w-full" />
        </div>
      ) : null}
    </>
  );
}

// --- Filters ---------------------------------------------------------------------------------

export function UsageFilters({
  preset,
  onPreset,
  custom,
  onCustom,
  problem,
  groupBy,
  onGroupBy,
  groups,
  range,
  interval,
  loading = false,
}: {
  preset: RangePreset;
  onPreset: (preset: RangePreset) => void;
  custom: DateRange;
  onCustom: (range: DateRange) => void;
  problem: string | null;
  groupBy: GroupBy;
  onGroupBy: (groupBy: GroupBy) => void;
  groups: readonly { value: GroupBy; label: string }[];
  range: DateRange;
  interval: Interval;
  loading?: boolean;
}) {
  const id = useId();
  const describedBy = problem ? `${id}-problem` : undefined;
  return (
    <div className="flex flex-col gap-1.5">
      <div role="group" aria-label="Filters" className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-period`}>Period</Label>
          <SimpleSelect id={`${id}-period`} value={preset} onChange={onPreset} options={RANGE_PRESETS} />
        </div>
        {preset === "custom" ? (
          <>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${id}-from`}>From</Label>
              <Input
                id={`${id}-from`}
                type="date"
                value={custom.from}
                max={custom.to || undefined}
                onChange={(event) => onCustom({ ...custom, from: event.target.value })}
                aria-invalid={problem ? true : undefined}
                aria-describedby={describedBy}
                className="w-40"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${id}-to`}>To</Label>
              <Input
                id={`${id}-to`}
                type="date"
                value={custom.to}
                min={custom.from || undefined}
                onChange={(event) => onCustom({ ...custom, to: event.target.value })}
                aria-invalid={problem ? true : undefined}
                aria-describedby={describedBy}
                className="w-40"
              />
            </div>
          </>
        ) : null}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-group`}>Group by</Label>
          <SimpleSelect id={`${id}-group`} value={groupBy} onChange={onGroupBy} options={groups} />
        </div>
        <p className="flex h-8 items-center gap-1.5 text-xs text-muted-foreground" aria-live="polite">
          {loading ? <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" /> : null}
          {problem ? null : `${rangeText(range)} · per ${interval}`}
        </p>
      </div>
      {problem ? (
        <p id={`${id}-problem`} className="text-xs text-destructive">
          {problem}
        </p>
      ) : null}
    </div>
  );
}

// --- Report ----------------------------------------------------------------------------------

/** Everything under the filters, for one summary. `stale` dims it while the next one loads. */
export function UsageReport({ summary, me, stale = false, now }: { summary: UsageSummary; me: Me; stale?: boolean; now?: Date }) {
  const admin = isUsageAdmin(me);
  const chart = costChart(summary);
  const note = unpricedNote(summary.totals);
  const range = { from: summary.from, to: summary.to };
  return (
    <div className={cn("flex flex-col gap-6 transition-opacity", stale && "opacity-60")} aria-busy={stale || undefined}>
      <div className="flex flex-col gap-2">
        <KpiTiles summary={summary} />
        {note ? (
          <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
            <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {note}
          </p>
        ) : null}
      </div>

      {summary.totals.requests === 0 ? (
        <EmptyState
          icon={Activity}
          title="No model usage in this period"
          description={`Nothing used an AI model between ${rangeText(range)}. Choose a longer period, or come back after people have used the assistant.`}
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-5">
          <section className="min-w-0 rounded-xl border bg-card p-4 lg:col-span-3" aria-label="Cost over time">
            {chart ? <AnalyticsChart data={chart} /> : <p className="text-sm text-muted-foreground">No periods to chart.</p>}
          </section>
          <section className="min-w-0 rounded-xl border bg-card p-4 lg:col-span-2" aria-label="Breakdown">
            <BreakdownChart groups={summary.groups} groupBy={summary.group_by} />
          </section>
        </div>
      )}

      <BudgetsPanel budgets={summary.budgets} meId={me.id} admin={admin} now={now} />

      {admin ? (
        <>
          <ChargebackPanel range={range} />
          <EventsPanel range={range} />
        </>
      ) : null}
    </div>
  );
}

export function KpiTiles({ summary }: { summary: Pick<UsageSummary, "totals"> }) {
  return (
    <dl className="grid gap-3 sm:grid-cols-3">
      {kpis(summary.totals).map((tile) => (
        <div key={tile.key} className="flex flex-col gap-1 rounded-xl border bg-card px-4 py-3">
          <dt className="text-sm text-muted-foreground">{tile.label}</dt>
          <dd className="text-2xl font-semibold tracking-tight">{tile.value}</dd>
          {tile.detail ? <dd className="text-xs text-muted-foreground">{tile.detail}</dd> : null}
        </div>
      ))}
    </dl>
  );
}

// --- Breakdown -------------------------------------------------------------------------------

/**
 * Ranked horizontal bars, largest first, in one hue (the groups are nominal: colour would only
 * repeat the length). Each bar prints its value and share, so nothing hides behind hover; the
 * table view adds requests and tokens for every group.
 */
export function BreakdownChart({ groups, groupBy }: { groups: readonly UsageGroup[]; groupBy: GroupBy }) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const titleId = useId();
  const { measure, rows } = breakdown(groups, groupBy);
  const { bars, hidden } = visibleBars(rows);
  const title = `${measure === "cost" ? "Cost" : "Requests"} by ${GROUP_LABELS[groupBy].toLowerCase()}`;
  return (
    <figure aria-labelledby={titleId} className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <figcaption id={titleId} className="text-sm font-medium">
          {title}
        </figcaption>
        <ViewToggle view={view} onChange={setView} />
      </div>
      {measure === "requests" ? (
        <p className="text-xs text-muted-foreground">Nothing in this period had a price, so the bars count requests.</p>
      ) : null}
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No groups to show.</p>
      ) : view === "chart" ? (
        <>
          <ol className="flex flex-col gap-2.5" aria-label={title}>
            {bars.map((row) => (
              <BarRow key={row.key} row={row} measure={measure} />
            ))}
          </ol>
          {hidden > 0 ? (
            <p className="text-xs text-muted-foreground">
              {hidden} more {hidden === 1 ? "group is" : "groups are"} in the table view.
            </p>
          ) : null}
        </>
      ) : (
        <BreakdownTable rows={rows} groupBy={groupBy} title={title} />
      )}
    </figure>
  );
}

function BarRow({ row, measure }: { row: BreakdownRow; measure: "cost" | "requests" }) {
  const value = measure === "cost" ? formatUsd(row.cost) : formatNumber(row.requests);
  return (
    <li className="flex flex-col gap-1 rounded-md px-1 py-0.5 hover:bg-muted/50">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="min-w-0 truncate" title={row.label}>
          {row.label}
        </span>
        <span className="shrink-0 tabular-nums">
          <span className="font-medium">{value}</span>
          {measure === "cost" ? <span className="ml-2 text-muted-foreground">{formatShare(row.share)}</span> : null}
        </span>
      </div>
      <div aria-hidden="true" className="h-3 w-full">
        {row.fraction > 0 ? (
          <div className="h-full rounded-r-[4px]" style={{ width: `${Math.max(0.5, row.fraction * 100)}%`, background: "var(--viz-1)" }} />
        ) : null}
      </div>
    </li>
  );
}

function BreakdownTable({ rows, groupBy, title }: { rows: readonly BreakdownRow[]; groupBy: GroupBy; title: string }) {
  return (
    <div className="max-h-96 overflow-auto rounded-lg border">
      <table className="w-full text-sm">
        <caption className="sr-only">{title}</caption>
        <thead className="sticky top-0 bg-muted/80 backdrop-blur">
          <tr>
            {[GROUP_LABELS[groupBy], "Cost", "Share of cost", "Requests", "Input tokens", "Output tokens"].map((name, i) => (
              <th key={name} scope="col" className={cn("px-3 py-1.5 font-medium whitespace-nowrap", i === 0 ? "text-left" : "text-right")}>
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-t">
              <th scope="row" className="max-w-56 truncate px-3 py-1.5 text-left font-normal" title={row.label}>
                {row.label}
              </th>
              <td className="px-3 py-1.5 text-right whitespace-nowrap tabular-nums">{formatUsd(row.cost)}</td>
              <td className="px-3 py-1.5 text-right whitespace-nowrap tabular-nums">{formatShare(row.share)}</td>
              <td className="px-3 py-1.5 text-right whitespace-nowrap tabular-nums">{formatNumber(row.requests)}</td>
              <td className="px-3 py-1.5 text-right whitespace-nowrap tabular-nums">{formatNumber(row.inputTokens)}</td>
              <td className="px-3 py-1.5 text-right whitespace-nowrap tabular-nums">{formatNumber(row.outputTokens)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- Budgets ---------------------------------------------------------------------------------

const MAX_BUDGETS = 6;

export function BudgetsPanel({ budgets, meId, admin, now }: { budgets: readonly BudgetStatus[]; meId: string; admin: boolean; now?: Date }) {
  const [all, setAll] = useState(false);
  const sorted = sortBudgets(budgets);
  const shown = all ? sorted : sorted.slice(0, MAX_BUDGETS);
  return (
    <section aria-labelledby="usage-budgets-heading" className="flex flex-col gap-4 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id="usage-budgets-heading" className="font-medium">
            Budgets
          </h3>
          <p className="text-sm text-muted-foreground">
            {admin
              ? "Spending limits on model usage, most urgent first. Each warns once per period when it reaches its warning level."
              : "The spending limits that apply here, most urgent first."}
          </p>
        </div>
        {admin ? (
          <Link href="/admin/budgets" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Manage budgets
          </Link>
        ) : null}
      </div>
      {sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {admin ? "No budgets are set, so model usage has no spending limit." : "No budget limits this usage."}
        </p>
      ) : (
        <ul className="grid gap-5 md:grid-cols-2">
          {shown.map((budget) => (
            <li key={budget.id}>
              <BudgetMeter budget={budget} meId={meId} now={now} />
            </li>
          ))}
        </ul>
      )}
      {sorted.length > MAX_BUDGETS ? (
        <Button variant="ghost" size="sm" className="w-fit" aria-expanded={all} onClick={() => setAll((current) => !current)}>
          {all ? "Show the most urgent only" : `Show all ${sorted.length} budgets`}
        </Button>
      ) : null}
    </section>
  );
}

// --- Chargeback (Enterprise) -----------------------------------------------------------------

export function ChargebackPanel({ range }: { range: DateRange }) {
  return (
    <section aria-labelledby="usage-chargeback-heading" className="flex flex-col gap-3 rounded-xl border bg-card p-4">
      <div>
        <h3 id="usage-chargeback-heading" className="flex items-center gap-2 font-medium">
          Chargeback report
          <EnterpriseBadge feature="chargeback_reports" />
        </h3>
        <p className="text-sm text-muted-foreground">Cost per department for {rangeText(range)}, as a CSV file for internal billing.</p>
      </div>
      <EnterpriseGate feature="chargeback_reports">
        <ChargebackDownload range={range} />
      </EnterpriseGate>
    </section>
  );
}

/** A real link (it works without JavaScript); with it, a 402 or other error is explained in place. */
export function ChargebackDownload({ range }: { range: DateRange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function download(event: MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const blob = await usageApi.chargebackCsv(range);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `chargeback-${range.from}-to-${range.to}.csv`;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <a
        href={chargebackPath(range)}
        download={`chargeback-${range.from}-to-${range.to}.csv`}
        onClick={(event) => void download(event)}
        aria-disabled={busy || undefined}
        className={cn(buttonVariants({ variant: "outline", size: "sm" }), "w-fit")}
      >
        {busy ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : <Download data-icon="inline-start" aria-hidden="true" />}
        {busy ? "Preparing the CSV…" : "Download chargeback CSV"}
      </a>
      {error ? isEnterpriseFeatureError(error) ? <EnterpriseNotice feature="chargeback_reports" /> : <ErrorAlert error={error} /> : null}
    </div>
  );
}

// --- Requests (admin) ------------------------------------------------------------------------

export function EventsPanel({ range }: { range: DateRange }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <section aria-labelledby={`${id}-heading`} className="flex flex-col gap-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id={`${id}-heading`} className="font-medium">
            Requests
          </h3>
          <p className="text-sm text-muted-foreground">Every model request in the period, newest first, with who made it and what it cost.</p>
        </div>
        <Button variant="outline" size="sm" aria-expanded={open} aria-controls={`${id}-table`} onClick={() => setOpen((current) => !current)}>
          <ReceiptText data-icon="inline-start" aria-hidden="true" />
          {open ? "Hide requests" : "Show requests"}
        </Button>
      </div>
      <div id={`${id}-table`}>{open ? <EventsTable key={`${range.from}:${range.to}`} range={range} /> : null}</div>
    </section>
  );
}

function EventsTable({ range }: { range: DateRange }) {
  const [list] = useState(() =>
    createPagedList<UsageEvent, string>(async (cursor) => {
      const page = await usageApi.events(range, cursor);
      return { items: page.items, next: page.next_cursor };
    }),
  );
  const state = useStore(list);
  if (state.status === "loading") return <TableSkeleton rows={4} />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void list.reload()} />;
  if (state.items.length === 0) return <p className="text-sm text-muted-foreground">No requests in this period.</p>;
  return (
    <div className="flex flex-col gap-3">
      <EventsRows items={state.items} />
      {state.moreError ? <ErrorAlert error={state.moreError} onRetry={() => void list.loadMore()} /> : null}
      {state.hasMore ? (
        <Button variant="outline" size="sm" className="w-fit self-center" onClick={() => void list.loadMore()} disabled={state.loadingMore}>
          {state.loadingMore ? "Loading…" : "Load more"}
        </Button>
      ) : null}
    </div>
  );
}

export function EventsRows({ items }: { items: readonly UsageEvent[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          <TableHead>Person</TableHead>
          <TableHead>Department</TableHead>
          <TableHead>Feature</TableHead>
          <TableHead>Model</TableHead>
          <TableHead className="text-right">Tokens in / out</TableHead>
          <TableHead className="text-right">Cost</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((event) => (
          <TableRow key={event.id}>
            <TableCell className="whitespace-nowrap">{formatDateTime(event.occurred_at)}</TableCell>
            <TableCell>{event.user?.name ?? <span className="text-muted-foreground">No person (system)</span>}</TableCell>
            <TableCell>{event.department?.name ?? <span className="text-muted-foreground">—</span>}</TableCell>
            <TableCell>
              {featureLabel(event.feature)}
              {event.agent_key ? <span className="block font-mono text-xs text-muted-foreground">{event.agent_key}</span> : null}
            </TableCell>
            <TableCell>
              <span className="font-mono text-xs">{event.model}</span>
              <span className="block text-xs text-muted-foreground">
                {event.provider}
                {event.local ? (
                  <Badge variant="outline" className="ml-1.5 h-4 px-1 text-[10px]">
                    local
                  </Badge>
                ) : null}
              </span>
            </TableCell>
            <TableCell className="text-right whitespace-nowrap tabular-nums">
              {formatNumber(event.input_tokens)} / {formatNumber(event.output_tokens)}
            </TableCell>
            <TableCell className="text-right whitespace-nowrap tabular-nums">
              {event.cost_usd === null ? <span className="text-muted-foreground">No price</span> : formatUsd(event.cost_usd)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
