"use client";

import { LoaderCircle, Square } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { waitingMessage } from "@/lib/analytics/ask";

/**
 * While an answer is being made (10–30 s, one request, no streaming): what is happening, the
 * elapsed time, an outline of the answer to come, and a way to stop waiting.
 */
export function AskProgress({ question, startedAt, onStop }: { question: string; startedAt: number; onStop: () => void }) {
  const [now, setNow] = useState(startedAt);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000));
  const message = waitingMessage(elapsed);

  return (
    <section aria-label="Answer in progress" aria-busy="true" className="flex flex-col gap-4 rounded-xl border bg-card p-5">
      <div className="flex items-start gap-3">
        <LoaderCircle className="mt-0.5 size-5 shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden="true" />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          {/* Announced when it changes (start, ~30 s, ~90 s), not every second. */}
          <div role="status" aria-live="polite">
            <p className="font-medium">{message.title}</p>
            <p className="text-sm text-muted-foreground">{message.detail}</p>
          </div>
          <p className="mt-1 truncate text-sm">“{question}”</p>
        </div>
        <span className="shrink-0 text-sm text-muted-foreground tabular-nums" aria-hidden="true">
          {elapsed} s
        </span>
      </div>
      <div className="flex flex-col gap-2" aria-hidden="true">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={onStop}>
          <Square className="fill-current" data-icon="inline-start" />
          Stop waiting
        </Button>
      </div>
    </section>
  );
}
