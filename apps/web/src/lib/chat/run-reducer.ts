/**
 * Pure reducer for a run's server-sent events (see runner.py for the order):
 * run.started → query.rewritten? → retrieval → token* → citation* → verification →
 * run.completed | run.failed | run.cancelled.
 *
 * Every event carries a stream id ("<ms>-<seq>"). Reconnects and re-attaches replay
 * events, so anything at or before the last applied id is ignored: tokens never double.
 */

import type { Citation, Source, Usage } from "@/lib/chat/api";

export type RunPhase = "connecting" | "searching" | "streaming" | "completed" | "failed" | "cancelled";

export interface RunState {
  runId: string;
  phase: RunPhase;
  lastEventId: string | null;
  /** Follow-up questions are rewritten into a standalone search query. */
  searchQuery: string | null;
  sources: Source[];
  withheld: number;
  text: string;
  citations: Citation[];
  unknownMarkers: string[];
  messageId: string | null;
  usage: Usage | null;
  abstained: boolean;
  error: { code: string; message: string } | null;
}

export interface RunEvent {
  /** SSE `id:` (EventSource exposes it as lastEventId). */
  id?: string | null;
  type: string;
  data: unknown;
}

export const TERMINAL_PHASES: ReadonlySet<RunPhase> = new Set(["completed", "failed", "cancelled"]);

export function initialRunState(runId: string): RunState {
  return {
    runId,
    phase: "connecting",
    lastEventId: null,
    searchQuery: null,
    sources: [],
    withheld: 0,
    text: "",
    citations: [],
    unknownMarkers: [],
    messageId: null,
    usage: null,
    abstained: false,
    error: null,
  };
}

export function isTerminal(state: RunState): boolean {
  return TERMINAL_PHASES.has(state.phase);
}

/** Compares Valkey stream ids "<ms>-<seq>" numerically; other strings fall back to text order. */
export function compareEventIds(a: string, b: string): number {
  const parse = (id: string) => {
    const match = /^(\d+)(?:-(\d+))?$/.exec(id);
    return match ? [Number(match[1]), Number(match[2] ?? 0)] : null;
  };
  const pa = parse(a);
  const pb = parse(b);
  if (pa && pb) return pa[0] - pb[0] || pa[1] - pb[1]; // ms timestamps fit in a double
  return a < b ? -1 : a > b ? 1 : 0;
}

function record(data: unknown): Record<string, unknown> {
  return typeof data === "object" && data !== null && !Array.isArray(data) ? (data as Record<string, unknown>) : {};
}

const str = (value: unknown): string | null => (typeof value === "string" ? value : null);
const num = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) ? value : 0);

export function reduceRunEvent(state: RunState, event: RunEvent): RunState {
  // Replays (reconnect, re-attach) and anything after the end are ignored.
  if (event.id && state.lastEventId && compareEventIds(event.id, state.lastEventId) <= 0) return state;
  if (isTerminal(state)) return state;

  const next: RunState = { ...state, lastEventId: event.id ?? state.lastEventId };
  const data = record(event.data);

  switch (event.type) {
    case "run.started":
      return { ...next, phase: "searching" };
    case "query.rewritten":
      return { ...next, searchQuery: str(data.query) };
    case "retrieval":
      return {
        ...next,
        phase: next.phase === "connecting" ? "searching" : next.phase,
        sources: Array.isArray(data.sources) ? (data.sources as Source[]) : [],
        withheld: num(data.withheld),
      };
    case "token": {
      const text = str(data.text) ?? "";
      return { ...next, phase: "streaming", text: next.text + text };
    }
    case "citation": {
      const marker = str(data.marker);
      if (!marker || next.citations.some((c) => c.marker === marker)) return next;
      return { ...next, citations: [...next.citations, data as unknown as Citation] };
    }
    case "verification":
      return {
        ...next,
        unknownMarkers: Array.isArray(data.unknown_markers) ? data.unknown_markers.filter((m): m is string => typeof m === "string") : [],
      };
    case "run.completed":
      return {
        ...next,
        phase: "completed",
        messageId: str(data.message_id),
        usage: record(data.usage) as Usage,
        abstained: data.abstained === true,
        withheld: typeof data.withheld === "number" ? data.withheld : next.withheld,
      };
    case "run.cancelled":
      return { ...next, phase: "cancelled", messageId: str(data.message_id) };
    case "run.failed":
      return {
        ...next,
        phase: "failed",
        messageId: str(data.message_id),
        error: { code: str(data.code) ?? "internal", message: str(data.message) ?? "Something went wrong while answering." },
      };
    default:
      return next; // unknown event types are ignored (forward compatible)
  }
}

export const RUN_EVENT_TYPES = [
  "run.started",
  "query.rewritten",
  "retrieval",
  "token",
  "citation",
  "verification",
  "run.completed",
  "run.failed",
  "run.cancelled",
] as const;
