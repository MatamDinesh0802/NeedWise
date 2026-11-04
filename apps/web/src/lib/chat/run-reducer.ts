/**
 * Pure reducer for a run's server-sent events (see runner.py for the order):
 * run.started → query.rewritten? → plan? → agent.step* → retrieval → token* → citation* →
 * verification → run.completed | run.failed | run.cancelled.
 * Agent steps are merged by agent and never go backwards, so their order doesn't matter.
 *
 * Every event carries a stream id ("<ms>-<seq>"). Reconnects and re-attaches replay
 * events, so anything at or before the last applied id is ignored: tokens never double.
 */

import type { Citation, Source, Usage } from "@/lib/chat/api";

export type RunPhase = "connecting" | "searching" | "streaming" | "completed" | "failed" | "cancelled";

export type PlanMode = "direct" | "single" | "fan_out";
export type RoutedBy = "fast_path" | "planner" | "fallback" | string;

export interface RunPlan {
  mode: PlanMode;
  routedBy: RoutedBy;
  subtasks: { agent: string; goal: string }[];
}

export type AgentStatus = "pending" | "running" | "done" | "failed";

export interface AgentStep {
  agent: string;
  goal: string;
  status: AgentStatus;
  /** The goal while running; the error when failed. */
  label: string;
  findings: number;
  sources: number;
  seconds: number | null;
}

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
  plan: RunPlan | null;
  /** One per agent, in plan order (agents seen before the plan are appended). */
  agents: AgentStep[];
  /** Server time (ms) of the first and last applied events, from the stream ids. */
  startedAtMs: number | null;
  endedAtMs: number | null;
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
    plan: null,
    agents: [],
    startedAtMs: null,
    endedAtMs: null,
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

/** Milliseconds part of a Valkey stream id ("1730000000000-3" → 1730000000000). */
export function eventTimeMs(id: string | null | undefined): number | null {
  const match = id ? /^(\d{10,})(?:-\d+)?$/.exec(id) : null;
  return match ? Number(match[1]) : null;
}

const STATUS_RANK: Record<AgentStatus, number> = { pending: 0, running: 1, done: 2, failed: 2 };

function upsertAgent(agents: AgentStep[], agent: string, patch: Partial<AgentStep>): AgentStep[] {
  const index = agents.findIndex((a) => a.agent === agent);
  const base: AgentStep =
    index === -1
      ? { agent, goal: "", status: "pending", label: "", findings: 0, sources: 0, seconds: null }
      : agents[index];
  // Never go backwards: a late "running" must not undo "done".
  if (patch.status && STATUS_RANK[patch.status] < STATUS_RANK[base.status]) {
    const { status: _ignored, ...rest } = patch;
    void _ignored;
    patch = { ...rest, label: base.label };
  }
  const next = { ...base, ...patch };
  return index === -1 ? [...agents, next] : agents.map((a, i) => (i === index ? next : a));
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

  const at = eventTimeMs(event.id);
  const next: RunState = {
    ...state,
    lastEventId: event.id ?? state.lastEventId,
    startedAtMs: state.startedAtMs ?? at,
  };
  const data = record(event.data);

  switch (event.type) {
    case "run.started":
      return { ...next, phase: "searching" };
    case "query.rewritten":
      return { ...next, searchQuery: str(data.query) };
    case "plan": {
      const subtasks = Array.isArray(data.subtasks)
        ? data.subtasks
            .map(record)
            .filter((t) => typeof t.agent === "string")
            .map((t) => ({ agent: t.agent as string, goal: str(t.goal) ?? "" }))
        : [];
      const mode = (["direct", "single", "fan_out"] as const).find((m) => m === data.mode) ?? "direct";
      // Merge with steps that may have arrived first; plan order wins, extras keep their place after.
      let agents: AgentStep[] = [];
      for (const task of subtasks) {
        const seen = next.agents.find((a) => a.agent === task.agent);
        agents = [...agents, seen ? { ...seen, goal: seen.goal || task.goal } : { agent: task.agent, goal: task.goal, status: "pending", label: task.goal, findings: 0, sources: 0, seconds: null }];
      }
      agents = [...agents, ...next.agents.filter((a) => !subtasks.some((t) => t.agent === a.agent))];
      return {
        ...next,
        phase: next.phase === "connecting" ? "searching" : next.phase,
        plan: { mode, routedBy: str(data.routed_by) ?? "planner", subtasks },
        agents,
      };
    }
    case "agent.step": {
      const agent = str(data.agent);
      const status = (["running", "done", "failed"] as const).find((s) => s === data.status);
      if (!agent || !status) return next;
      const label = str(data.label) ?? "";
      const patch: Partial<AgentStep> =
        status === "running"
          ? { status, label, goal: next.agents.find((a) => a.agent === agent)?.goal || label }
          : {
              status,
              label,
              findings: num(data.findings),
              sources: num(data.sources),
              seconds: typeof data.seconds === "number" ? data.seconds : null,
            };
      return {
        ...next,
        phase: next.phase === "connecting" ? "searching" : next.phase,
        agents: upsertAgent(next.agents, agent, patch),
      };
    }
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
        endedAtMs: at,
        phase: "completed",
        messageId: str(data.message_id),
        usage: record(data.usage) as Usage,
        abstained: data.abstained === true,
        withheld: typeof data.withheld === "number" ? data.withheld : next.withheld,
      };
    case "run.cancelled":
      return { ...next, endedAtMs: at, phase: "cancelled", messageId: str(data.message_id) };
    case "run.failed":
      return {
        ...next,
        endedAtMs: at,
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
  "plan",
  "agent.step",
  "retrieval",
  "token",
  "citation",
  "verification",
  "run.completed",
  "run.failed",
  "run.cancelled",
] as const;
