"use client";

import { CalendarCheck2, CalendarClock, Play } from "lucide-react";
import Link from "next/link";

import { ErrorAlert, RefreshingHint } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-api-query";
import { meetingsApi, meetingsKeys, type Commitment } from "@/lib/meetings/api";
import {
  commitmentStatus,
  dueOf,
  formatDay,
  formatTimestamp,
  isCommitmentClosed,
  meetingLink,
  sortCommitments,
} from "@/lib/meetings/meetings";
import { cn } from "@/lib/utils";

/** "My commitments": what I said I'd do in meetings, with the moment I said it. */
export function Commitments() {
  const { state, refetch } = useApiQuery(meetingsKeys.commitments, meetingsApi.commitments);

  return (
    <section aria-labelledby="commitments-heading" className="flex flex-col gap-2 rounded-xl border p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 id="commitments-heading" className="flex items-center gap-2 font-medium">
            <CalendarCheck2 className="size-4 text-muted-foreground" aria-hidden="true" />
            My commitments
            {state.status === "success" && state.data.length > 0 ? (
              <span className="text-sm font-normal text-muted-foreground">({state.data.length})</span>
            ) : null}
          </h3>
          <p className="text-xs text-muted-foreground">
            Action items from meetings that name you as the owner. Open the moment to hear it again.
          </p>
        </div>
        <RefreshingHint active={state.status === "success" && state.refreshing} />
      </div>
      {state.status === "loading" ? (
        <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading your commitments">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={() => void refetch()} />
      ) : state.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing yet. When a meeting&apos;s notes name you as the owner of an action item, it shows up here.{" "}
          <Link href="/meetings" className="underline underline-offset-2">
            Go to meetings
          </Link>
        </p>
      ) : (
        <ul className="flex flex-col divide-y">
          {sortCommitments(state.data).map((c) => (
            <CommitmentRow key={`${c.meeting_id}-${c.index}`} commitment={c} />
          ))}
        </ul>
      )}
    </section>
  );
}

function CommitmentRow({ commitment: c }: { commitment: Commitment }) {
  const closed = isCommitmentClosed(c);
  const due = dueOf(c.due);
  const status = commitmentStatus(c);
  return (
    <li className={cn("flex flex-wrap items-start justify-between gap-2 py-2", closed && "opacity-70")}>
      <div className="min-w-0 flex-1">
        <p className={cn("text-sm break-words", closed && "line-through decoration-muted-foreground/50")}>{c.text}</p>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <Link
            href={meetingLink(c.meeting_id, c.start)}
            className="inline-flex items-center gap-1 rounded underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
          >
            {c.start != null ? <Play className="size-3" aria-hidden="true" /> : null}
            <span>{c.meeting_title}</span>
            {c.start != null ? <span className="font-mono tabular-nums">at {formatTimestamp(c.start)}</span> : null}
          </Link>
          <span>{formatDay(c.meeting_date)}</span>
          {due ? (
            <span className={cn("flex items-center gap-1", due.overdue && !closed && "text-destructive")}>
              <CalendarClock className="size-3" aria-hidden="true" />
              {due.text}
            </span>
          ) : (
            <span>No deadline</span>
          )}
        </p>
      </div>
      {c.task_id ? (
        <Link
          href={`/tasks/${c.task_id}`}
          className="rounded-full focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
          aria-label={`${status}: open the task`}
        >
          <Badge variant="secondary" className="font-normal">
            {status}
          </Badge>
        </Link>
      ) : (
        <Badge variant="outline" className="font-normal text-muted-foreground">
          {status}
        </Badge>
      )}
    </li>
  );
}
