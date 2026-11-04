/**
 * When to keep auto-refreshing a document list. Pure, so the stop conditions are tested.
 */

import type { DocumentStatus } from "@/lib/knowledge/api";

export const LIVE_REFRESH_MS = 2500;
/** Give up after this long, so a stuck document can't make the page poll forever. */
export const LIVE_REFRESH_MAX_MS = 10 * 60 * 1000;
export const LIVE_REFRESH_MAX_ERRORS = 3;

const IN_FLIGHT: ReadonlySet<DocumentStatus> = new Set(["pending", "processing"]);

export function isInFlight(status: DocumentStatus): boolean {
  return IN_FLIGHT.has(status);
}

export function needsLiveRefresh(documents: readonly { status: DocumentStatus }[]): boolean {
  return documents.some((d) => isInFlight(d.status));
}

export type LiveDecision =
  | "poll"
  /** Tab hidden: skip this tick, keep the timer. */
  | "pause"
  /** Nothing pending or processing anymore. */
  | "stop-settled"
  | "stop-timeout"
  | "stop-errors";

export function liveRefreshDecision({
  documents,
  elapsedMs,
  visible,
  consecutiveErrors,
}: {
  documents: readonly { status: DocumentStatus }[];
  elapsedMs: number;
  visible: boolean;
  consecutiveErrors: number;
}): LiveDecision {
  if (!needsLiveRefresh(documents)) return "stop-settled";
  if (consecutiveErrors >= LIVE_REFRESH_MAX_ERRORS) return "stop-errors";
  if (elapsedMs >= LIVE_REFRESH_MAX_MS) return "stop-timeout";
  if (!visible) return "pause";
  return "poll";
}
