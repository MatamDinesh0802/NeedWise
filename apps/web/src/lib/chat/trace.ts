/**
 * Agent trace for one answer: which department agents ran, what each found, how long it
 * took. Built from the live run (stored messages don't keep it). Pure and tested.
 */

import type { AgentStep, RoutedBy, RunPlan, RunState } from "@/lib/chat/run-reducer";

export interface AgentTrace {
  plan: RunPlan | null;
  steps: AgentStep[];
  /** Wall-clock time of the whole run (server clock), when known. */
  totalSeconds: number | null;
}

/**
 * Only runs where agents actually worked get a trace: fan-out plans, or any run where an
 * agent reported a step. A "single" plan in auto mode is answered by the plain pipeline
 * and sends no agent.step, so its plan-seeded "pending" entries don't count.
 */
export function traceFromRun(run: RunState): AgentTrace | null {
  const reported = run.agents.some((a) => a.status !== "pending");
  if (!reported && run.plan?.mode !== "fan_out") return null;
  const total =
    run.startedAtMs !== null && run.endedAtMs !== null && run.endedAtMs >= run.startedAtMs
      ? (run.endedAtMs - run.startedAtMs) / 1000
      : null;
  return { plan: run.plan, steps: run.agents, totalSeconds: total };
}

/** "support" → "Support agent", "it-ops" → "It ops agent", "finance-agent" → "Finance agent". */
export function agentName(key: string): string {
  const words = key.replace(/[-_]+/g, " ").trim().replace(/\s+agent$/i, "");
  if (!words) return "Agent";
  return `${words.charAt(0).toUpperCase()}${words.slice(1)} agent`;
}

/** 8.1 → "8.1 s", 19.4 → "19 s", 75 → "1 min 15 s". */
export function formatSeconds(seconds: number): string {
  if (seconds < 10) return `${Math.max(0, seconds).toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
}

/** One sentence for the drawer about how the question was split. */
export function planSentence(plan: RunPlan): string {
  const parts = `${plan.subtasks.length} ${plan.subtasks.length === 1 ? "part" : "parts"}`;
  switch (plan.routedBy) {
    case "fast_path":
      return `Matched instantly to the agents' example questions (${parts}), without asking the planner.`;
    case "fallback":
      return `The planner was unavailable, so a fallback plan split the question into ${parts}.`;
    default:
      return `The planner split the question into ${parts}.`;
  }
}

export function routedByLabel(routedBy: RoutedBy | null | undefined): string {
  switch (routedBy) {
    case "fast_path":
      return "matched instantly";
    case "planner":
      return "planned";
    case "fallback":
      return "fallback plan (the planner was unavailable)";
    default:
      return "planned";
  }
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Why a failed agent didn't contribute, calmly. */
export function failureText(step: AgentStep): string {
  return /time(d)?\s*out|timeout/i.test(step.label) ? "timed out; answered without it" : "unavailable; answered without it";
}

/** One live line: "Finance agent: hotel limit in London… ✓ 6 findings · 8.1 s". */
export function stepLine(step: AgentStep): { name: string; goal: string; status: string } {
  const name = agentName(step.agent);
  const goal = step.goal || step.label;
  switch (step.status) {
    case "pending":
      return { name, goal, status: "waiting" };
    case "running":
      return { name, goal, status: "searching…" };
    case "done":
      return {
        name,
        goal,
        status: [plural(step.findings, "finding"), step.seconds !== null ? formatSeconds(step.seconds) : null]
          .filter(Boolean)
          .join(" · "),
      };
    case "failed":
      return { name, goal, status: failureText(step) };
  }
}

/** Collapsed line after the run: "Answered by 3 agents · 19 s". */
export function traceSummary(trace: AgentTrace): string {
  const total = trace.steps.length;
  const failed = trace.steps.filter((s) => s.status === "failed").length;
  const answered = total - failed;
  const who =
    total === 0
      ? "Answered by the agents"
      : failed === 0
        ? `Answered by ${plural(total, "agent")}`
        : `Answered by ${answered} of ${plural(total, "agent")}`;
  const longest = Math.max(0, ...trace.steps.map((s) => s.seconds ?? 0));
  const seconds = trace.totalSeconds ?? (longest > 0 ? longest : null);
  return seconds !== null ? `${who} · ${formatSeconds(seconds)}` : who;
}

/** True while some agent is still working (pending or running). */
export function agentsWorking(trace: AgentTrace): boolean {
  return trace.steps.some((s) => s.status === "pending" || s.status === "running");
}
