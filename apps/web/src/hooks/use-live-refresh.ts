"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { DocumentStatus } from "@/lib/knowledge/api";
import { LIVE_REFRESH_MS, liveRefreshDecision, needsLiveRefresh, type LiveDecision } from "@/lib/knowledge/live";
import { getQueryCache } from "@/lib/stores/query-cache";

/**
 * Re-fetches `key` every ~2.5 s while any document is pending or processing.
 * Stops when everything settled, after repeated errors, or after 10 minutes;
 * pauses while the tab is hidden. `resume()` restarts after a stop.
 */
export function useLiveRefresh(key: string, documents: readonly { status: DocumentStatus }[] | null) {
  const active = documents !== null && needsLiveRefresh(documents);
  const latest = useRef(documents);
  useEffect(() => {
    latest.current = documents;
  });
  const [stopped, setStopped] = useState<Extract<LiveDecision, "stop-timeout" | "stop-errors"> | null>(null);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (!active || stopped) return;
    const cache = getQueryCache();
    const startedAt = Date.now();
    let errors = 0;
    let busy = false;
    const timer = setInterval(async () => {
      if (busy) return;
      const decision = liveRefreshDecision({
        documents: latest.current ?? [],
        elapsedMs: Date.now() - startedAt,
        visible: document.visibilityState === "visible",
        consecutiveErrors: errors,
      });
      if (decision === "pause") return;
      if (decision !== "poll") {
        clearInterval(timer);
        if (decision !== "stop-settled") setStopped(decision);
        return;
      }
      busy = true;
      await cache.refetch(key);
      busy = false;
      errors = cache.getSnapshot(key).status === "error" ? errors + 1 : 0;
    }, LIVE_REFRESH_MS);
    return () => clearInterval(timer);
  }, [active, stopped, key, generation]);

  const resume = useCallback(() => {
    setStopped(null);
    setGeneration((g) => g + 1);
  }, []);

  return { live: active && !stopped, stopped: active ? stopped : null, resume };
}
