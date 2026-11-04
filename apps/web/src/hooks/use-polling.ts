"use client";

import { useEffect } from "react";

import { getQueryCache } from "@/lib/stores/query-cache";

/** Give up after this long, so something stuck can't make a page poll forever. */
const MAX_POLL_MS = 10 * 60 * 1000;

/**
 * Re-fetches the cached query `key` every `intervalMs` while `active` (e.g. a sync or a workflow
 * run is in progress). Skips ticks while the tab is hidden; stops after 10 minutes.
 */
export function usePolling(key: string | null, active: boolean, intervalMs = 3000) {
  useEffect(() => {
    if (!key || !active) return;
    const cache = getQueryCache();
    const startedAt = Date.now();
    let busy = false;
    const timer = setInterval(async () => {
      if (Date.now() - startedAt > MAX_POLL_MS) {
        clearInterval(timer);
        return;
      }
      if (busy || document.visibilityState !== "visible") return;
      busy = true;
      await cache.refetch(key);
      busy = false;
    }, intervalMs);
    return () => clearInterval(timer);
  }, [key, active, intervalMs]);
}
