/**
 * Framework-free store around one run's EventSource. Opens on the first subscription,
 * feeds every event through the pure reducer, and closes the EventSource after a
 * terminal event (otherwise the browser would reconnect forever).
 */

import { chatApi } from "@/lib/chat/api";
import {
  initialRunState,
  isTerminal,
  reduceRunEvent,
  RUN_EVENT_TYPES,
  type RunState,
} from "@/lib/chat/run-reducer";

export interface EventSourceLike {
  addEventListener(type: string, listener: (event: { data: string; lastEventId?: string }) => void): void;
  close(): void;
  onerror: ((event: unknown) => void) | null;
  readonly readyState: number;
}

/** EventSource.CLOSED: the browser gave up (e.g. 404 for an expired run). */
const CLOSED = 2;

export interface RunStream {
  readonly runId: string;
  getSnapshot: () => RunState;
  subscribe: (listener: () => void) => () => void;
  /** Stop listening (unmount). Doesn't cancel the run on the server. */
  close: () => void;
}

export function createRunStream(
  runId: string,
  {
    createEventSource = (url: string) => new EventSource(url) as unknown as EventSourceLike,
    onTerminal,
  }: {
    createEventSource?: (url: string) => EventSourceLike;
    /** Called once when the run ends (refetch the conversation for stored ids). */
    onTerminal?: (state: RunState) => void;
  } = {},
): RunStream {
  let state = initialRunState(runId);
  let source: EventSourceLike | null = null;
  let finished = false;
  const listeners = new Set<() => void>();

  const set = (next: RunState) => {
    if (next === state) return;
    state = next;
    for (const listener of listeners) listener();
  };

  function finish() {
    if (finished) return;
    finished = true;
    source?.close();
    source = null;
    onTerminal?.(state);
  }

  function open() {
    if (source || finished) return;
    source = createEventSource(chatApi.eventsUrl(runId));
    for (const type of RUN_EVENT_TYPES) {
      source.addEventListener(type, (event) => {
        let data: unknown = null;
        try {
          data = JSON.parse(event.data);
        } catch {
          return; // malformed frame: ignore
        }
        set(reduceRunEvent(state, { id: event.lastEventId || null, type, data }));
        if (isTerminal(state)) finish();
      });
    }
    source.onerror = () => {
      // CONNECTING: the browser reconnects with Last-Event-ID; the reducer drops replays.
      // CLOSED: it won't retry (run expired or not ours); report and let the page refetch.
      if (source && source.readyState === CLOSED && !finished) {
        set({
          ...state,
          phase: "failed",
          error: { code: "stream_lost", message: "Lost the connection to this answer." },
        });
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
