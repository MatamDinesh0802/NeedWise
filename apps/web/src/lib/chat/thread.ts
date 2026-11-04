/**
 * Pure helpers for the conversation thread: which run to attach to, what to render,
 * and which sources an answer cited.
 */

import type { ConversationDetail, Message, Source } from "@/lib/chat/api";
import { citedMarkers, markerSortKey } from "@/lib/chat/citations";
import type { RunState } from "@/lib/chat/run-reducer";

/**
 * On load (or after a reload mid-answer) the API reports `active_run_id`.
 * Attach to it unless we are already following that run.
 */
export function runToAttach(detail: Pick<ConversationDetail, "active_run_id">, followingRunId: string | null): string | null {
  const active = detail.active_run_id;
  return active && active !== followingRunId ? active : null;
}

/** True once the stored conversation contains the answer for this run. */
export function hasStoredAnswer(messages: readonly Message[], runId: string): boolean {
  return messages.some((m) => m.role === "assistant" && m.run_id === runId);
}

export type ThreadItem =
  | { kind: "message"; key: string; message: Message }
  | { kind: "live"; key: string; run: RunState };

/**
 * Stored messages, then (while a run is still live) its question if not stored yet and
 * the live answer. Once the stored answer arrives, the live item disappears.
 */
export function threadItems(
  messages: readonly Message[],
  live: RunState | null,
  pendingUser: Message | null,
): ThreadItem[] {
  const items: ThreadItem[] = messages.map((message) => ({ kind: "message", key: message.id, message }));
  if (live && !hasStoredAnswer(messages, live.runId)) {
    if (pendingUser && !messages.some((m) => m.id === pendingUser.id)) {
      items.push({ kind: "message", key: pendingUser.id, message: pendingUser });
    }
    items.push({ kind: "live", key: `live-${live.runId}`, run: live });
  }
  return items;
}

/** The question that started a run (for "Retry"). */
export function questionForRun(messages: readonly Message[], runId: string | null): string | null {
  if (!runId) return null;
  return messages.find((m) => m.role === "user" && m.run_id === runId)?.content ?? null;
}

export interface SourceGroups {
  cited: Source[];
  /** Retrieved but not cited: shown as "also searched". */
  other: Source[];
}

/** Splits sources into cited and uncited, each in marker order (S1, S2, …). */
export function groupSources(
  sources: readonly Source[],
  answerText: string,
  citations: readonly { marker: string }[] = [],
): SourceGroups {
  const cited = new Set([...citedMarkers(answerText), ...citations.map((c) => c.marker)]);
  const byMarker = (a: Source, b: Source) => markerSortKey(a.marker) - markerSortKey(b.marker);
  const unique = [...new Map(sources.map((s) => [s.marker, s])).values()];
  return {
    cited: unique.filter((s) => cited.has(s.marker)).sort(byMarker),
    other: unique.filter((s) => !cited.has(s.marker)).sort(byMarker),
  };
}
