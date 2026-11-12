"use client";

import { CalendarClock, CalendarCheck2, Play } from "lucide-react";
import Link from "next/link";

import { ErrorAlert } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-api-query";
import { formatDate } from "@/lib/format";
import { meetingsApi, meetingsKeys, type Commitment } from "@/lib/meetings/api";
import { formatTimestamp, meetingLink, TASK_STATUS_TEXT } from "@/lib/meetings/meetings";
import { cn } from "@/lib/utils";
import { dueText } from "@/lib/work/tasks";

/** Open commitments first (by due date), finished ones last. */
export function sortCommitments(items: readonly Commitment[]): Commitment[] {
  const closed = (c: Commitment) => (c.task_status === "done" || c.task_status === "cancelled" ? 1 : 0);
  const due = (c: Commitment) => (c.due ? Date.parse(`${c.due}T17:00:00Z`) : Number.POSITIVE_INFINITY);
  return [...items].sort((a, b) => closed(a) - closed(b) || due(a) - due(b));
}

/** "My commitments": what I said I'd do in meetings, with the moment I said it. */
export function Commitments() {
  const { state, refetch } = useApiQuery(meetingsKeys.commitments, meetingsApi.commitments);
  if (state.status === "loading") return <Skeleton className="h-24 w-full rounded-xl" />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  if (state.data.length === 0) return null;
  const items = sortCommitments(state.data);

  return (
    <section aria-labelledby="commitments-heading" className="flex flex-col gap-2 rounded-xl border p-4">
      <div>
        <h3 id="commitments-heading" className="flex items-center gap-2 font-medium">
          <CalendarCheck2 className="size-4 text-muted-foreground" aria-hidden="true" />
          My commitments <span className="text-sm font-normal text-muted-foreground">({items.length})</span>
        </h3>
        <p className="text-xs text-muted-foreground">What you agreed to do in meetings. Open the moment to hear it again.</p>
      </div>
      <ul className="flex flex-col divide-y">
        {items.map((c) => {
          const due = c.due ? dueText(`${c.due}T17:00:00Z`) : null;
          const done = c.task_status === "done" || c.task_status === "cancelled";
          return (
            <li key={`${c.meeting_id}-${c.index}`} className={cn("flex flex-wrap items-start justify-between gap-2 py-2", done && "opacity-70")}>
              <div className="min-w-0 flex-1">
                <p className={cn("text-sm", done && "line-through decoration-muted-foreground/50")}>{c.text}</p>
                <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                  <Link href={meetingLink(c.meeting_id, c.start)} className="inline-flex items-center gap-1 underline-offset-2 hover:underline">
                    {c.start != null ? <Play className="size-3" aria-hidden="true" /> : null}
                    {c.meeting_title}
                    {c.start != null ? <span className="font-mono tabular-nums">at {formatTimestamp(c.start)}</span> : null}
                  </Link>
                  <span>{formatDate(c.meeting_date)}</span>
                  {due ? (
                    <span className={cn("flex items-center gap-1", due.overdue && !done && "text-destructive")}>
                      <CalendarClock className="size-3" aria-hidden="true" />
                      {due.text}
                    </span>
                  ) : null}
                </p>
              </div>
              {c.task_id ? (
                <Link href={`/tasks/${c.task_id}`}>
                  <Badge variant="secondary" className="font-normal">
                    {TASK_STATUS_TEXT[c.task_status ?? ""] ?? "Task"}
                  </Badge>
                </Link>
              ) : (
                <Badge variant="outline" className="font-normal text-muted-foreground">
                  Not a task yet
                </Badge>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
