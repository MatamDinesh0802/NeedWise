import {
  parseSystemStatus,
  SYSTEM_STATUS_ENDPOINT,
  SYSTEM_STATUS_REFRESH_MS,
  type StatusFetchState,
} from "@/lib/system-status";

export interface StatusPollerOptions {
  onUpdate: (state: StatusFetchState) => void;
  intervalMs?: number;
  timeoutMs?: number;
  endpoint?: string;
  fetchImpl?: typeof fetch;
  /** Polls are skipped while this returns false (e.g. a hidden browser tab). */
  isVisible?: () => boolean;
  now?: () => Date;
}

export interface StatusPoller {
  /** Fetch now. Resolves once the result (or error) has been reported. */
  refresh: () => Promise<void>;
  /** Stop polling, abort any in-flight request, and never report again. */
  stop: () => void;
}

/**
 * Framework-free poller for the system status endpoint. Starts immediately.
 * Only the latest request may report; superseded or stopped requests are dropped.
 */
export function createStatusPoller({
  onUpdate,
  intervalMs = SYSTEM_STATUS_REFRESH_MS,
  timeoutMs = 5_000,
  endpoint = SYSTEM_STATUS_ENDPOINT,
  fetchImpl = (...args) => fetch(...args),
  isVisible = () => true,
  now = () => new Date(),
}: StatusPollerOptions): StatusPoller {
  let current: AbortController | null = null;
  let stopped = false;

  async function load(): Promise<void> {
    if (stopped) return;
    current?.abort();
    const controller = new AbortController();
    current = controller;
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);

    try {
      const response = await fetchImpl(endpoint, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = parseSystemStatus(await response.json());
      if (current === controller) onUpdate({ kind: "success", data, checkedAt: now() });
    } catch (error) {
      if (current !== controller) return;
      const message = timedOut
        ? "request timed out"
        : error instanceof Error && error.message
          ? error.message
          : "network error";
      onUpdate({ kind: "error", message, checkedAt: now() });
    } finally {
      clearTimeout(timeout);
      if (current === controller) current = null;
    }
  }

  const interval = setInterval(() => {
    if (isVisible()) void load();
  }, intervalMs);
  void load();

  return {
    refresh: load,
    stop() {
      stopped = true;
      clearInterval(interval);
      const pending = current;
      current = null;
      pending?.abort();
    },
  };
}
