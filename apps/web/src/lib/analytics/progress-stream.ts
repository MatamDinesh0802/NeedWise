/**
 * Framework-free store around one Insights answer's EventSource (GET
 * /api/v1/analytics/runs/{id}/events): opens on the first subscription, feeds each event through
 * `reduceProgress`, and closes after run.completed or run.failed (otherwise the browser would
 * reconnect forever). The same pattern as the chat's run stream.
 */

import type { EventSourceLike } from "@/lib/chat/run-stream";

import { analyticsApi } from "./api";
import { initialProgress, isProgressDone, PROGRESS_EVENT_TYPES, reduceProgress, type ProgressState } from "./progress";

const CLOSED = 2; // EventSource.CLOSED: the browser gave up (an expired or unknown run)

export interface ProgressStream {
  readonly runId: string;
  getSnapshot: () => ProgressState;
  subscribe: (listener: () => void) => () => void;
  /** Stop listening. The answer is still made and saved on the server. */
  close: () => void;
}

export function createProgressStream(
  runId: string,
  {
    createEventSource = (url: string) => new EventSource(url) as unknown as EventSourceLike,
    onDone,
  }: {
    createEventSource?: (url: string) => EventSourceLike;
    /** Called once when the run ends (fetch the stored answer). */
    onDone?: (state: ProgressState) => void;
  } = {},
): ProgressStream {
  let state = initialProgress();
  let source: EventSourceLike | null = null;
  let finished = false;
  const listeners = new Set<() => void>();

  const set = (next: ProgressState) => {
    if (next === state) return;
    state = next;
    for (const listener of listeners) listener();
  };

  function finish() {
    if (finished) return;
    finished = true;
    source?.close();
    source = null;
    onDone?.(state);
  }

  function open() {
    if (source || finished) return;
    source = createEventSource(analyticsApi.eventsUrl(runId));
    for (const type of PROGRESS_EVENT_TYPES) {
      source.addEventListener(type, (event) => {
        let data: unknown = null;
        try {
          data = JSON.parse(event.data);
        } catch {
          return; // a malformed frame: ignore it
        }
        set(reduceProgress(state, { id: event.lastEventId || null, type, data }));
        if (isProgressDone(state)) finish();
      });
    }
    source.onerror = () => {
      // CONNECTING: the browser reconnects with Last-Event-ID and the reducer drops replays.
      // CLOSED: it won't retry; report it, and let the page look for the saved answer.
      if (source && source.readyState === CLOSED && !finished) {
        set({ ...state, phase: "failed", error: { code: "stream_lost", message: "Lost the connection to this answer." } });
        finish();
      }
    };
  }

  return {
    runId,
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      open();
      return () => listeners.delete(listener);
    },
    close() {
      finished = true;
      source?.close();
      source = null;
    },
  };
}
