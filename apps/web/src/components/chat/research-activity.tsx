"use client";

import { CircleCheck, CircleX, LoaderCircle } from "lucide-react";

import { ApprovalById } from "@/components/work/approval-card";
import { toolActivity } from "@/lib/chat/research";
import type { ApprovalRef, ToolStep } from "@/lib/chat/run-reducer";
import { cn } from "@/lib/utils";
import type { Approval } from "@/lib/work/api";

/** What the research agent did, as a compact list: "Searched company documents ×4 ✓". */
export function ToolActivity({ steps, live }: { steps: readonly ToolStep[]; live: boolean }) {
  const lines = toolActivity(steps);
  if (lines.length === 0 && !live) return null;
  return (
    <ul className="flex flex-col gap-1 text-xs text-muted-foreground" aria-label="What the assistant did">
      {lines.map((line) => (
        <li key={line.key} className={cn("flex items-center gap-1.5", !line.ok && "text-destructive")}>
          {line.ok ? (
            <CircleCheck className="size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-label="done" />
          ) : (
            <CircleX className="size-3.5 shrink-0" aria-label="failed" />
          )}
          <span>
            {line.label}
            {line.count > 1 ? <span className="tabular-nums"> ×{line.count}</span> : null}
          </span>
        </li>
      ))}
      {live ? (
        <li className="flex items-center gap-1.5" role="status">
          <LoaderCircle className="size-3.5 shrink-0 animate-spin" aria-hidden="true" />
          Working…
        </li>
      ) : null}
    </ul>
  );
}

/**
 * The actions a research run proposed, as approval cards. `refs` come from the live run (or this
 * tab's memory of it); `stored` are approvals fetched from the API for the same run (after a reload).
 */
export function RunApprovals({
  refs,
  stored,
  meId,
}: {
  refs: readonly ApprovalRef[];
  stored: readonly Approval[];
  meId: string | null;
}) {
  const ids = [...new Set([...refs.map((r) => r.id), ...stored.map((a) => a.id)])];
  if (ids.length === 0) return null;
  return (
    <section className="flex flex-col gap-3" aria-label="Actions waiting for approval">
      {ids.map((id) => (
        <ApprovalById
          key={id}
          id={id}
          initial={stored.find((a) => a.id === id)}
          meId={meId}
          compact
          fallbackSummary={refs.find((r) => r.id === id)?.summary}
        />
      ))}
    </section>
  );
}
