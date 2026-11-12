"use client";

import { ArrowRight, RefreshCw } from "lucide-react";
import Link from "next/link";

import { BudgetMeter } from "@/components/usage/budget-meter";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-api-query";
import { formatNumber } from "@/lib/analytics/format";
import type { Me } from "@/lib/session";
import { cn } from "@/lib/utils";
import { usageApi, usageKeys, type SummaryParams, type UsageSummary } from "@/lib/usage/api";
import { presetRange } from "@/lib/usage/dates";
import { budgetsForMe, formatUsd, myUsage, sortBudgets } from "@/lib/usage/format";

/** Overview: "Your usage this month", grouped by person so admins and managers also get their own share. */
export function UsageTile({ me }: { me: Me }) {
  const params: SummaryParams = { ...presetRange("this_month"), group_by: "user", interval: "day" };
  const { state, refetch } = useApiQuery(usageKeys.summary(params), () => usageApi.summary(params));
  return (
    <section aria-labelledby="usage-tile-heading" className="rounded-xl border bg-card p-4">
      <h3 id="usage-tile-heading" className="text-sm font-medium">
        Your usage this month
      </h3>
      {state.status === "loading" ? (
        <div className="mt-2 flex flex-col gap-2" aria-busy="true" aria-label="Loading your usage">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-2 w-full" />
        </div>
      ) : state.status === "error" ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          Your usage couldn&apos;t be loaded.
          <Button variant="ghost" size="xs" onClick={() => void refetch()}>
            <RefreshCw data-icon="inline-start" />
            Retry
          </Button>
        </div>
      ) : (
        <UsageTileBody summary={state.data} me={me} />
      )}
    </section>
  );
}

/** The tile's figures for one summary (for tests: no fetching). */
export function UsageTileBody({ summary, me, now }: { summary: UsageSummary; me: Pick<Me, "id" | "departments">; now?: Date }) {
  const mine = myUsage(summary, me.id);
  const tightest = sortBudgets(budgetsForMe(summary.budgets, me))[0] ?? null;
  return (
    <div className="mt-2 grid gap-4 sm:grid-cols-[minmax(10rem,auto)_1fr_auto] sm:items-end">
      <p className="flex flex-col">
        <span className="text-2xl font-semibold tracking-tight">{formatUsd(mine.cost_usd)}</span>
        <span className="text-xs text-muted-foreground">
          {formatNumber(mine.requests)} {mine.requests === 1 ? "request" : "requests"}
          {mine.unpriced_requests ? `, ${formatNumber(mine.unpriced_requests)} without a price` : ""}
        </span>
      </p>
      <div className="min-w-0">
        {tightest ? (
          <BudgetMeter budget={tightest} meId={me.id} now={now} />
        ) : (
          <p className="text-xs text-muted-foreground">No budget limits your usage.</p>
        )}
      </div>
      <Link href="/analytics" className={cn(buttonVariants({ variant: "outline", size: "sm" }), "w-fit")}>
        See usage
        <ArrowRight data-icon="inline-end" aria-hidden="true" />
      </Link>
    </div>
  );
}
