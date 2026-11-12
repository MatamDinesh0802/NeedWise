/** One view model for an assistant answer, live (from a run) or stored (from history). */

import type { Citation, Message, Source, Usage } from "@/lib/chat/api";
import type { ApprovalRef, RunError, RunState, ToolStep } from "@/lib/chat/run-reducer";
import { traceFromRun, type AgentTrace } from "@/lib/chat/trace";

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
  /** Research runs: tool calls (live or seen in this tab) and the actions they proposed. */
  toolSteps: ToolStep[];
  approvals: ApprovalRef[];
}

/** What a finished research run leaves behind in this tab (stored messages don't keep it). */
export interface ResearchActivity {
  toolSteps: ToolStep[];
  approvals: ApprovalRef[];
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
  research: ResearchActivity | null = null,
): AnswerView {
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
    error: message.status === "failed" ? { code: "failed", message: "This answer failed." } : null,
    message,
    trace,
    toolSteps: research?.toolSteps ?? [],
    approvals: research?.approvals ?? [],
  };
}

export const ABSTAIN_FALLBACK = "I couldn't find this in the sources available to you.";
