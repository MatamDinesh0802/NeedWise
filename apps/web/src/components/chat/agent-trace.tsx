"use client";

import { Bot, ChevronRight, CircleAlert, CircleCheck, CircleDashed, LoaderCircle, Route } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { AgentStep } from "@/lib/chat/run-reducer";
import {
  agentName,
  failureText,
  formatSeconds,
  planSentence,
  stepLine,
  traceSummary,
  type AgentTrace,
} from "@/lib/chat/trace";
import { cn } from "@/lib/utils";

function StepIcon({ status }: { status: AgentStep["status"] }) {
  const className = "size-4 shrink-0";
  switch (status) {
    case "pending":
      return <CircleDashed className={cn(className, "text-muted-foreground")} aria-hidden="true" />;
    case "running":
      return <LoaderCircle className={cn(className, "animate-spin text-sky-600 dark:text-sky-400")} aria-hidden="true" />;
    case "done":
      return <CircleCheck className={cn(className, "text-emerald-600 dark:text-emerald-400")} aria-hidden="true" />;
    case "failed":
      return <CircleAlert className={cn(className, "text-amber-600 dark:text-amber-400")} aria-hidden="true" />;
  }
}

/** Compact live list while agents work: "Finance agent: hotel limit in London… ✓ 6 findings · 8.1 s". */
export function AgentTraceLive({ trace }: { trace: AgentTrace }) {
  return (
    <ul className="flex flex-col gap-1 rounded-xl border bg-muted/20 px-3 py-2" aria-label="Agents working on this answer">
      {trace.steps.map((step) => {
        const line = stepLine(step);
        return (
          <li key={step.agent} className="flex items-center gap-2 text-xs">
            <StepIcon status={step.status} />
            <span className="shrink-0 font-medium">{line.name}:</span>
            <span className="min-w-0 flex-1 truncate text-muted-foreground" title={line.goal}>
              {line.goal}
            </span>
            <span
              className={cn(
                "shrink-0 tabular-nums",
                step.status === "failed" ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground",
              )}
            >
              {line.status}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** After the run: one line that opens the trace drawer. */
export function AgentTraceSummary({ trace }: { trace: AgentTrace }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="ghost"
        size="xs"
        className="w-fit gap-1 px-1.5 text-muted-foreground"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
      >
        <Bot data-icon="inline-start" />
        {traceSummary(trace)}
        <ChevronRight className="size-3" aria-hidden="true" />
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-md">
          <SheetHeader className="border-b">
            <SheetTitle>How this answer was made</SheetTitle>
            <SheetDescription>
              {traceSummary(trace)}. Each agent searched only its collections, as you: never with more access than you have.
            </SheetDescription>
          </SheetHeader>
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {trace.plan ? (
              <p className="flex items-start gap-2 text-sm">
                <Route className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span>{planSentence(trace.plan)}</span>
              </p>
            ) : null}
            <ol className="space-y-3">
              {trace.steps.map((step) => (
                <li key={step.agent} className="space-y-1.5 rounded-xl border p-3">
                  <div className="flex items-center gap-2">
                    <StepIcon status={step.status} />
                    <span className="font-medium">{agentName(step.agent)}</span>
                    <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                      {step.seconds !== null ? formatSeconds(step.seconds) : null}
                    </span>
                  </div>
                  {step.goal ? <p className="text-sm text-muted-foreground">{step.goal}</p> : null}
                  {step.status === "failed" ? (
                    <p className="text-xs text-amber-700 dark:text-amber-400">
                      {failureText(step)}
                      {step.label && step.label !== step.goal ? ` (${step.label})` : ""}
                    </p>
                  ) : step.status === "done" ? (
                    <p className="text-xs text-muted-foreground">
                      {step.findings} {step.findings === 1 ? "finding" : "findings"} · {step.sources}{" "}
                      {step.sources === 1 ? "source" : "sources"}
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">Didn&apos;t finish.</p>
                  )}
                </li>
              ))}
            </ol>
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
