"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { createStatusPoller, type StatusPoller } from "@/lib/status-poller";
import { SYSTEM_STATUS_REFRESH_MS, type StatusFetchState } from "@/lib/system-status";

/** Subscribes to the system status poller (every ~10 s, paused while the tab is hidden). */
export function useSystemStatus(intervalMs: number = SYSTEM_STATUS_REFRESH_MS) {
  const [state, setState] = useState<StatusFetchState>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const poller = useRef<StatusPoller | null>(null);

  useEffect(() => {
    const instance = createStatusPoller({
      intervalMs,
      onUpdate: setState,
      isVisible: () => document.visibilityState === "visible",
    });
    poller.current = instance;
    return () => {
      instance.stop();
      if (poller.current === instance) poller.current = null;
    };
  }, [intervalMs]);

  /** Manual refresh: same as a poll, but shows a spinner. */
  const refresh = useCallback(async () => {
    const instance = poller.current;
    if (!instance) return;
    setRefreshing(true);
    try {
      await instance.refresh();
    } finally {
      setRefreshing(false);
    }
  }, []);

  return { state, refreshing, refresh };
}
