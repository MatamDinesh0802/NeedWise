"use client";

import { Check, ChevronRight, CircleAlert, Circle, LoaderCircle, Square } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { waitingMessage } from "@/lib/analytics/ask";
import { progressAnnouncement, progressSteps, type ProgressState, type ProgressStep } from "@/lib/analytics/progress";
import { cn } from "@/lib/utils";

/**
 * While an answer is being made: the steps the server reports as it goes (planning, each query
 * with its SQL and outcome, writing, checking every figure, the chart), the elapsed time, and a
 * way to stop waiting (the answer is still saved).
 */
export function AskProgress({
  question,
  startedAt,
  progress,
  onStop,
}: {
  question: string;
  startedAt: number;
  progress: ProgressState;
  onStop: () => void;
}) {
  const [now, setNow] = useState(startedAt);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000));
  const slow = elapsed >= 30 ? waitingMessage(elapsed) : null;
  const steps = progressSteps(progress);

  return (
    <section aria-label="Answer in progress" aria-busy="true" className="flex flex-col gap-4 rounded-xl border bg-card p-5">
      <div className="flex items-start gap-3">
        <LoaderCircle className="mt-0.5 size-5 shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden="true" />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <p className="font-medium">Working on your question</p>
          <p className="truncate text-sm">“{question}”</p>
          {/* The current step, announced when it changes (not every second). */}
          <p role="status" aria-live="polite" className="sr-only">
            {progressAnnouncement(progress)}
          </p>
        </div>
        <span className="shrink-0 text-sm text-muted-foreground tabular-nums" aria-hidden="true">
          {elapsed} s
        </span>
      </div>

      <ol className="flex flex-col gap-2" aria-label="Steps">
        {steps.map((step) => (
          <StepRow key={step.key} step={step} />
        ))}
      </ol>

      {slow ? <p className="text-sm text-muted-foreground">{slow.detail}</p> : null}

      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={onStop}>
          <Square className="fill-current" data-icon="inline-start" />
          Stop waiting
        </Button>
      </div>
    </section>
  );
}

const STATUS_WORDS = { pending: "Not started", active: "In progress", done: "Done", failed: "Failed" } as const;

function StepRow({ step }: { step: ProgressStep }) {
  const [showSql, setShowSql] = useState(false);
  const Icon = step.status === "done" ? Check : step.status === "failed" ? CircleAlert : step.status === "active" ? LoaderCircle : Circle;
  return (
    <li className="flex items-start gap-2.5 text-sm">
      <Icon
        aria-hidden="true"
        className={cn(
          "mt-0.5 size-4 shrink-0",
          step.status === "done" && "text-emerald-700 dark:text-emerald-400",
          step.status === "failed" && "text-destructive",
          step.status === "active" && "animate-spin text-muted-foreground motion-reduce:animate-none",
          step.status === "pending" && "text-muted-foreground/50",
        )}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className={cn(step.status === "pending" && "text-muted-foreground")}>
          {step.label}
          <span className="sr-only"> ({STATUS_WORDS[step.status]})</span>
        </span>
        {step.detail ? <span className="text-xs break-words text-muted-foreground">{step.detail}</span> : null}
        {step.sql ? (
          <>
            <button
              type="button"
              className="inline-flex w-fit items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
              aria-expanded={showSql}
              onClick={() => setShowSql((s) => !s)}
            >
              <ChevronRight className={cn("size-3 transition-transform", showSql && "rotate-90")} aria-hidden="true" />
              SQL
            </button>
            {showSql ? (
              <pre className="max-h-48 overflow-auto rounded-md border bg-muted/40 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
                {step.sql}
              </pre>
            ) : null}
          </>
        ) : null}
      </div>
    </li>
  );
}
