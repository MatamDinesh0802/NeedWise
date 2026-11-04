"use client";

import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { LiveDecision } from "@/lib/knowledge/live";

/** "Live" pulse while documents are processing, or why auto-refresh stopped. */
export function LiveIndicator({
  live,
  stopped,
  onResume,
}: {
  live: boolean;
  stopped: Extract<LiveDecision, "stop-timeout" | "stop-errors"> | null;
  onResume: () => void;
}) {
  if (stopped) {
    return (
      <span className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
        {stopped === "stop-errors"
          ? "Auto-refresh stopped after repeated errors."
          : "Still processing after 10 minutes; auto-refresh paused."}
        <Button variant="outline" size="xs" onClick={onResume}>
          <RefreshCw data-icon="inline-start" />
          Resume
        </Button>
      </span>
    );
  }
  if (!live) return null;
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
      <span className="relative flex size-2">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-sky-400 opacity-75" />
        <span className="relative inline-flex size-2 rounded-full bg-sky-500" />
      </span>
      Live: updating while documents are processed
    </span>
  );
}
