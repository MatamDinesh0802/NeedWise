/**
 * Research runs ("Research & act"): the tool calls, in words. Pure, so labels are tested.
 * Tool names come from apps/api/src/workmind/modules/tools/builtin.py and modules/work/tools.py;
 * remote (MCP) tools keep their own name.
 */

import type { ToolStep } from "@/lib/chat/run-reducer";

export type ChatMode = "answer" | "research";

const DONE: Record<string, string> = {
  search_knowledge: "Searched company documents",
  web_search: "Searched the web",
  draft_email: "Drafted an email",
  // Side effects never run from chat: calling them stores a proposal for approval.
  send_email: "Prepared an email for your approval",
  create_task: "Prepared a task for your approval",
};

const FAILED: Record<string, string> = {
  search_knowledge: "Couldn't search company documents",
  web_search: "Couldn't search the web",
  draft_email: "Couldn't draft the email",
  send_email: "Couldn't prepare the email",
  create_task: "Couldn't prepare the task",
};

/** API error codes from tools (ToolError.code), in words. */
const ERRORS: Record<string, string> = {
  forbidden: "not allowed for you",
  invalid_input: "invalid input",
  not_configured: "not set up",
  upstream_unavailable: "service unavailable",
  timeout: "took too long",
  // A research run executes at most 12 tool calls (MAX_TOOL_CALLS); later ones are not run.
  limit_reached: "skipped: the run's tool-call limit was reached",
};

function readable(tool: string): string {
  return tool.replace(/[._-]+/g, " ").trim() || "a tool";
}

/**
 * A call that became an approval. The API reports it as not run (`ok: false`,
 * `approval_required`): true, but for the person it is done, it waits in a card below.
 */
export function isProposal(step: ToolStep): boolean {
  return !step.ok && step.error === "approval_required";
}

export function toolStepLabel(step: ToolStep): string {
  if (step.ok) return DONE[step.tool] ?? `Used ${readable(step.tool)}`;
  if (isProposal(step)) return DONE[step.tool] ?? `Prepared ${readable(step.tool)} for your approval`;
  const base = FAILED[step.tool] ?? `${readable(step.tool).replace(/^./, (c) => c.toUpperCase())} failed`;
  const reason = step.error ? (ERRORS[step.error] ?? step.error.replace(/_/g, " ")) : null;
  return reason ? `${base} (${reason})` : base;
}

/** Repeated steps fold into one line: four searches → "Searched company documents ×4". */
export interface ToolActivityLine {
  key: string;
  label: string;
  ok: boolean;
  count: number;
}

export function toolActivity(steps: readonly ToolStep[]): ToolActivityLine[] {
  const lines: ToolActivityLine[] = [];
  for (const step of steps) {
    const label = toolStepLabel(step);
    const ok = step.ok || isProposal(step);
    const last = lines[lines.length - 1];
    if (last && last.label === label && last.ok === ok) {
      last.count += 1;
    } else {
      lines.push({ key: `${lines.length}-${step.tool}`, label, ok, count: 1 });
    }
  }
  return lines;
}

export const MODE_LABELS: Record<ChatMode, string> = {
  answer: "Answer",
  research: "Research & act",
};

export const MODE_HINTS: Record<ChatMode, string> = {
  answer: "Cited answers from documents you can access.",
  research: "Searches, uses tools and can propose actions. Nothing is sent or changed until you approve it.",
};
