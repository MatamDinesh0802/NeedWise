/** Small formatting helpers for the chat. Pure and tested. */

import type { Usage } from "@/lib/chat/api";

const COUNT = new Intl.NumberFormat("en-US");

export function formatCost(cost: number): string {
  if (cost === 0) return "$0";
  if (cost < 0.001) return "<$0.001";
  if (cost < 1) return `$${cost.toFixed(3)}`;
  return `$${cost.toFixed(2)}`;
}

/** "2,417 in · 339 out · $0.017 · claude-opus-5-5"; null when no model was called. */
export function formatUsage(usage: Usage | null | undefined): string | null {
  if (!usage) return null;
  const input = usage.input_tokens ?? 0;
  const output = usage.output_tokens ?? 0;
  if (input === 0 && output === 0 && !usage.model) return null;
  const parts = [`${COUNT.format(input)} in`, `${COUNT.format(output)} out`];
  if (typeof usage.cost_usd === "number") parts.push(formatCost(usage.cost_usd));
  if (usage.model) parts.push(usage.model);
  return parts.join(" · ");
}

export function withheldNotice(count: number): string | null {
  if (count <= 0) return null;
  return count === 1
    ? "1 restricted source matched, but your organization's policy keeps restricted content away from external AI models, so it wasn't used."
    : `${count} restricted sources matched, but your organization's policy keeps restricted content away from external AI models, so they weren't used.`;
}
