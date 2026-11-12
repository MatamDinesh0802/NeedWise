/** One view model for an assistant answer, live (from a run) or stored (from history). */

import type { Citation, Message, Source, Usage } from "@/lib/chat/api";
import type { ApprovalRef, RunError, RunState, ToolStep } from "@/lib/chat/run-reducer";
import { traceFromRun, type AgentTrace } from "@/lib/chat/trace";
import type { Approval } from "@/lib/work/api";

export type AnswerPhase = "searching" | "streaming" | "complete" | "cancelled" | "failed";

export interface AnswerView {
  key: string;
  runId: string | null;
  phase: AnswerPhase;
  text: string;
  sources: Source[];
  citations: Citation[];
  /** The rewritten search query, only when it differs from the question. */
  searchedFor: string | null;
  withheld: number;
  abstained: boolean;
  usage: Usage | null;
  error: RunError | null;
  /** Stored message (feedback needs its id); null while live. */
  message: Message | null;
  /** Department agents behind the answer; only known for runs seen live in this tab. */
  trace: AgentTrace | null;
  /** Research runs: tool calls (live, or stored with the message) and the actions they proposed. */
  toolSteps: ToolStep[];
  approvals: ApprovalRef[];
}

/** What a research run did: its tool calls, and the actions it proposed for approval. */
export interface ResearchActivity {
  toolSteps: ToolStep[];
  approvals: ApprovalRef[];
}

/** The steps stored with a research answer (`tool_steps`), as the live run showed them. */
export function researchFromMessage(message: Pick<Message, "tool_steps">): ResearchActivity | null {
  const steps = message.tool_steps ?? [];
  if (steps.length === 0) return null;
  return {
    toolSteps: steps.map((s) => ({ tool: s.tool, ok: s.ok, error: s.error ?? null })),
    approvals: steps.flatMap((s) => (s.approval_id ? [{ id: s.approval_id, tool: s.tool, summary: s.summary ?? "" }] : [])),
  };
}

/** A conversation's approvals, grouped by the run (answer) that proposed them. */
export function approvalsByRunId(approvals: readonly Approval[], conversationId: string): Map<string, Approval[]> {
  const byRun = new Map<string, Approval[]>();
  for (const approval of approvals) {
    const runId = approval.origin.run_id;
    if (approval.origin.conversation_id !== conversationId || typeof runId !== "string") continue;
    byRun.set(runId, [...(byRun.get(runId) ?? []), approval]);
  }
  return byRun;
}

export function answerFromRun(run: RunState): AnswerView {
  const phase: AnswerPhase =
    run.phase === "connecting" || run.phase === "searching"
      ? "searching"
      : run.phase === "streaming"
        ? "streaming"
        : run.phase === "completed"
          ? "complete"
          : run.phase;
  return {
    key: `live-${run.runId}`,
    runId: run.runId,
    phase,
    text: run.text,
    sources: run.sources,
    citations: run.citations,
    searchedFor: run.searchQuery,
    withheld: run.withheld,
    abstained: run.abstained,
    usage: run.usage,
    error: run.error,
    message: null,
    trace: traceFromRun(run),
    toolSteps: run.toolSteps,
    approvals: run.approvals,
  };
}

export function answerFromMessage(
  message: Message,
  question: string | null,
  trace: AgentTrace | null = null,
  /** This tab's memory of the live run; otherwise what the message stored. */
  research: ResearchActivity | null = null,
): AnswerView {
  const activity = research ?? researchFromMessage(message);
  const searchedFor =
    message.search_query && question && message.search_query.trim() !== question.trim() ? message.search_query : null;
  return {
    key: message.id,
    runId: message.run_id,
    phase: message.status,
    text: message.content,
    sources: message.sources ?? [],
    citations: message.citations ?? [],
    searchedFor,
    withheld: message.withheld ?? 0,
    abstained: message.abstained,
    usage: message.usage ?? null,
    error: message.status === "failed" ? storedError(message) : null,
    message,
    trace,
    toolSteps: activity?.toolSteps ?? [],
    approvals: activity?.approvals ?? [],
  };
}

const FAILED = "This answer failed.";

/**
 * Why a stored answer failed, as its run said it: a used-up budget keeps its budget, so the
 * thread shows which one and when it resets after the run ends or the page reloads. Answers
 * stored before the API kept this get a generic line.
 */
function storedError(message: Message): RunError {
  const stored = message.error;
  if (!stored || typeof stored.code !== "string" || !stored.code) return { code: "failed", message: FAILED };
  const text = typeof stored.message === "string" && stored.message ? stored.message : FAILED;
  return { code: stored.code, message: text, budget: (stored.budget as RunError["budget"]) ?? null };
}

export const ABSTAIN_FALLBACK = "I couldn't find this in the sources available to you.";
