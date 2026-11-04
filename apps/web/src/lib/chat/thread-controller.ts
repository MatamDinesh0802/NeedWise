/**
 * Framework-free controller for one open conversation (consumed via useSyncExternalStore):
 * loads it, attaches to an active run (e.g. after a reload mid-answer), sends questions,
 * cancels, retries, and refetches once a run ends so stored message ids are available.
 */

import { ApiError } from "@/lib/api";
import type { ResearchActivity } from "@/lib/chat/answer";
import { chatApi, type ConversationDetail, type Message } from "@/lib/chat/api";
import type { ChatMode } from "@/lib/chat/research";
import type { RunState } from "@/lib/chat/run-reducer";
import { createRunStream, type RunStream } from "@/lib/chat/run-stream";
import { hasStoredAnswer, runToAttach } from "@/lib/chat/thread";
import { traceFromRun, type AgentTrace } from "@/lib/chat/trace";

export interface ThreadState {
  status: "idle" | "loading" | "ready" | "not_found" | "error";
  conversation: ConversationDetail | null;
  error: unknown;
  live: RunState | null;
  /** The question just sent, until the refetched conversation contains it. */
  pendingUser: Message | null;
  sending: boolean;
  /** Agent traces of runs that ended in this tab, by run id (stored messages don't keep them). */
  traces: Readonly<Record<string, AgentTrace>>;
  /** Tool calls and proposed actions of research runs that ended in this tab, by run id. */
  research: Readonly<Record<string, ResearchActivity>>;
}

export interface ThreadDeps {
  api: Pick<typeof chatApi, "getConversation" | "createConversation" | "sendMessage" | "cancelRun">;
  createStream: (runId: string, onTerminal: () => void) => RunStream;
}

export interface ThreadController {
  getSnapshot: () => ThreadState;
  subscribe: (listener: () => void) => () => void;
  send: (content: string, mode?: ChatMode) => Promise<void>;
  cancel: () => Promise<void>;
  /** Replace a stored message (e.g. after feedback). */
  updateMessage: (message: Message) => void;
  dispose: () => void;
}

const defaultDeps: ThreadDeps = {
  api: chatApi,
  createStream: (runId, onTerminal) => createRunStream(runId, { onTerminal }),
};

export function createThreadController({
  conversationId: initialId,
  deps = defaultDeps,
  onConversationCreated,
  onActivity,
  onBusy,
  onRunEnded,
}: {
  conversationId: string | null;
  deps?: ThreadDeps;
  /** A new conversation was created for the first question (navigate to it). */
  onConversationCreated?: (id: string) => void;
  /** Titles or ordering may have changed (refresh the sidebar). */
  onActivity?: () => void;
  /** 409 run_in_progress: still answering. */
  onBusy?: () => void;
  /** A followed run ended (e.g. refresh approvals it proposed). */
  onRunEnded?: (run: RunState) => void;
}): ThreadController {
  let conversationId = initialId;
  const blank = (): ThreadState => ({
    status: initialId ? "idle" : "ready",
    conversation: null,
    error: null,
    live: null,
    pendingUser: null,
    sending: false,
    traces: {},
    research: {},
  });
  let state: ThreadState = blank();
  let stream: RunStream | null = null;
  let unsubscribeStream: (() => void) | null = null;
  let generation = 0;
  /** Runs that already ended here: never re-attach, even if the API still reports them. */
  const finished = new Set<string>();
  const listeners = new Set<() => void>();

  const set = (patch: Partial<ThreadState>) => {
    state = { ...state, ...patch };
    for (const listener of listeners) listener();
  };

  function detach() {
    unsubscribeStream?.();
    unsubscribeStream = null;
    stream?.close();
    stream = null;
  }

  function attach(runId: string) {
    if (stream?.runId === runId) return;
    detach();
    const current = deps.createStream(runId, () => {
      finished.add(runId);
      const run = current.getSnapshot();
      const trace = traceFromRun(run);
      if (trace) set({ traces: { ...state.traces, [runId]: trace } });
      if (run.toolSteps.length > 0 || run.approvals.length > 0) {
        set({ research: { ...state.research, [runId]: { toolSteps: run.toolSteps, approvals: run.approvals } } });
      }
      onRunEnded?.(run);
      void refetch();
    });
    stream = current;
    set({ live: current.getSnapshot() });
    unsubscribeStream = current.subscribe(() => {
      if (stream === current) set({ live: current.getSnapshot() });
    });
  }

  /** Applies a fetched conversation: drops the live run once its answer is stored, attaches to an active one. */
  function apply(detail: ConversationDetail) {
    const live = state.live;
    const stored = live ? hasStoredAnswer(detail.messages, live.runId) : false;
    set({
      status: "ready",
      conversation: detail,
      error: null,
      live: stored ? null : live,
      pendingUser: stored || (state.pendingUser && detail.messages.some((m) => m.id === state.pendingUser?.id)) ? null : state.pendingUser,
    });
    if (stored) detach();
    const runId = runToAttach(detail, stream?.runId ?? null);
    if (runId && !finished.has(runId)) attach(runId);
  }

  async function load() {
    if (!conversationId) return;
    const current = ++generation;
    if (!state.conversation) set({ status: "loading" }); // a re-subscribe keeps showing what we have
    try {
      const detail = await deps.api.getConversation(conversationId);
      if (current === generation) apply(detail);
    } catch (error) {
      if (current !== generation) return;
      set({ status: error instanceof ApiError && error.status === 404 ? "not_found" : "error", error });
    }
  }

  async function refetch() {
    if (!conversationId) return;
    const current = ++generation;
    try {
      const detail = await deps.api.getConversation(conversationId);
      if (current === generation) apply(detail);
    } catch {
      // Keep what we have; the live run already shows the outcome.
    }
    onActivity?.();
  }

  return {
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      if (state.status === "idle") void load();
      return () => listeners.delete(listener);
    },
    async send(content, mode = "answer") {
      const question = content.trim();
      if (!question || state.sending || (state.live && !["completed", "failed", "cancelled"].includes(state.live.phase))) {
        return;
      }
      set({ sending: true, error: null });
      try {
        let createdNew = false;
        if (!conversationId) {
          conversationId = (await deps.api.createConversation()).id;
          createdNew = true;
        }
        const started = await deps.api.sendMessage(conversationId, question, mode);
        set({ sending: false, pendingUser: started.user_message });
        attach(started.run_id);
        onActivity?.();
        if (createdNew) onConversationCreated?.(conversationId);
      } catch (error) {
        set({ sending: false });
        if (error instanceof ApiError && error.code === "run_in_progress") {
          onBusy?.();
          void refetch(); // pick up the run that is still going
          return;
        }
        throw error;
      }
    },
    async cancel() {
      const runId = stream?.runId;
      if (runId) await deps.api.cancelRun(runId);
    },
    updateMessage(message) {
      if (!state.conversation) return;
      set({
        conversation: {
          ...state.conversation,
          messages: state.conversation.messages.map((m) => (m.id === message.id ? message : m)),
        },
      });
    },
    dispose() {
      generation++;
      detach();
      if (!initialId) {
        // "New chat": Next keeps hidden routes mounted, so coming back must show a blank thread.
        conversationId = null;
        finished.clear();
        state = blank();
      } else {
        // A later subscribe (route shown again, StrictMode remount) reloads and re-attaches.
        state = { ...state, status: "idle" };
      }
    },
  };
}
