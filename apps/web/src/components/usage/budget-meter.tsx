"use client";

import { CircleCheck, OctagonX, TriangleAlert, type LucideIcon } from "lucide-react";
import { useId } from "react";

import { cn } from "@/lib/utils";
import type { BudgetState, BudgetStatus } from "@/lib/usage/api";
import { budgetTitle, percent, resetText, spentText, STATUS_TEXT, statusDetail, usedShare } from "@/lib/usage/format";

/**
 * Meter fill by severity (dataviz: accent → warning → critical); the track is a lighter step of
 * the same colour, so the state reads across the whole bar. Status colours never mean a series.
 */
const FILL: Record<BudgetState, string> = {
  ok: "var(--viz-1)",
  warning: "var(--viz-warning)",
  exceeded: "var(--viz-critical)",
};

const ICON: Record<BudgetState, LucideIcon> = {
  ok: CircleCheck,
  warning: TriangleAlert,
  exceeded: OctagonX,
};

const ICON_COLOUR: Record<BudgetState, string> = {
  ok: "var(--viz-good)",
  warning: "var(--viz-warning)",
  exceeded: "var(--viz-critical)",
};

/** The status as an icon plus words: never colour alone. */
export function BudgetStatusLabel({ budget, className }: { budget: BudgetStatus; className?: string }) {
  const Icon = ICON[budget.status] ?? CircleCheck;
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)} data-status={budget.status}>
      <Icon className="size-3.5 shrink-0" style={{ color: ICON_COLOUR[budget.status] }} aria-hidden="true" />
      <span>{statusDetail(budget)}</span>
    </span>
  );
}

/** A thin meter of spent against the limit, with a tick at the warning level. */
export function Meter({ budget, labelledBy, className }: { budget: BudgetStatus; labelledBy: string; className?: string }) {
  const share = usedShare(budget);
  const fill = FILL[budget.status] ?? FILL.ok;
  return (
    <div
      role="meter"
      aria-labelledby={labelledBy}
      aria-valuemin={0}
      aria-valuemax={budget.limit_usd}
      aria-valuenow={Math.min(budget.spent_usd, budget.limit_usd)}
      aria-valuetext={`${spentText(budget)}. ${STATUS_TEXT[budget.status] ?? ""}`}
      className={cn("relative h-2 w-full overflow-hidden rounded-full", className)}
      style={{ background: `color-mix(in oklab, ${fill} 22%, transparent)` }}
    >
      <div className="h-full rounded-full" style={{ width: `${Math.min(1, share) * 100}%`, background: fill }} />
      {budget.warn_at > 0 && budget.warn_at < 1 ? (
        <span
          aria-hidden="true"
          className="absolute inset-y-0 w-0.5 bg-foreground/45"
          style={{ left: `calc(${budget.warn_at * 100}% - 1px)` }}
          title={`Warns at ${percent(budget.warn_at)}`}
        />
      ) : null}
    </div>
  );
}

/** One budget: its name, spend against the limit, a meter, the status in words and the reset time. */
export function BudgetMeter({ budget, meId, now }: { budget: BudgetStatus; meId: string | null; now?: Date }) {
  const titleId = useId();
  return (
    <div className="flex flex-col gap-1.5" data-budget={budget.id}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <p id={titleId} className="text-sm font-medium">
          {budgetTitle(budget, meId)}
        </p>
        <p className="text-sm text-muted-foreground tabular-nums">{spentText(budget)}</p>
      </div>
      <Meter budget={budget} labelledBy={titleId} />
      <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
        <BudgetStatusLabel budget={budget} className="text-foreground" />
        <span>{resetText(budget.resets_at, now)}</span>
      </p>
    </div>
  );
}
