import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import type { ConversationDetail, Message } from "./api";
import { createRunStream, type EventSourceLike } from "./run-stream";
import { createThreadController, type ThreadDeps } from "./thread-controller";

function msg(overrides: Partial<Message>): Message {
  return {
    id: "m",
    role: "user",
    content: "",
    status: "complete",
    created_at: "2026-11-04T09:00:00Z",
    run_id: null,
    search_query: null,
    citations: [],
    sources: [],
    usage: {},
    abstained: false,
    withheld: 0,
    feedback: null,
    feedback_comment: null,
    ...overrides,
  };
}

const detail = (messages: Message[], active: string | null = null): ConversationDetail => ({
  id: "c1",
  title: "Parental leave",
  created_at: "2026-11-04T09:00:00Z",
  last_message_at: "2026-11-04T09:00:00Z",
  messages,
  active_run_id: active,
});

class FakeES implements EventSourceLike {
  listeners = new Map<string, ((e: { data: string; lastEventId?: string }) => void)[]>();
  onerror: ((e: unknown) => void) | null = null;
  readyState = 1;
  closed = false;
  constructor(public url: string) {}
  addEventListener(type: string, l: (e: { data: string; lastEventId?: string }) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), l]);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, id: string, data: unknown) {
    for (const l of this.listeners.get(type) ?? []) l({ data: JSON.stringify(data), lastEventId: id });
  }
}

function harness(api: Partial<ThreadDeps["api"]>) {
  const sources: FakeES[] = [];
  const deps: ThreadDeps = {
    api: {
      getConversation: vi.fn(),
      createConversation: vi.fn(),
      sendMessage: vi.fn(),
      cancelRun: vi.fn(async () => undefined),
      ...api,
    } as ThreadDeps["api"],
    createStream: (runId, onTerminal) =>
      createRunStream(runId, {
        onTerminal,
        createEventSource: (url) => {
          const es = new FakeES(url);
          sources.push(es);
          return es;
        },
      }),
  };
  return { deps, sources };
}

const question = msg({ id: "u1", role: "user", content: "How many weeks?", run_id: "r1" });
const answer = msg({ id: "a1", role: "assistant", content: "16 weeks [S1].", run_id: "r1" });

describe("thread controller", () => {
  it("attaches to the active run on load (e.g. a reload mid-answer) and settles on the stored answer", async () => {
    const getConversation = vi
      .fn()
      .mockResolvedValueOnce(detail([question], "r1")) // load: still answering
      .mockResolvedValueOnce(detail([question, answer], null)); // refetch after the run ended
    const { deps, sources } = harness({ getConversation });
    const onActivity = vi.fn();
    const controller = createThreadController({ conversationId: "c1", deps, onActivity });
    controller.subscribe(() => {});

    await vi.waitFor(() => expect(sources).toHaveLength(1));
    expect(sources[0].url).toBe("/api/v1/runs/r1/events");
    expect(controller.getSnapshot().live?.runId).toBe("r1");

    sources[0].emit("token", "1-0", { text: "16 weeks " });
    expect(controller.getSnapshot().live?.text).toBe("16 weeks ");
    sources[0].emit("run.completed", "2-0", { message_id: "a1", usage: {}, abstained: false, withheld: 0 });

    await vi.waitFor(() => expect(controller.getSnapshot().live).toBeNull());
    expect(controller.getSnapshot().conversation?.messages.map((m) => m.id)).toEqual(["u1", "a1"]);
    expect(sources[0].closed).toBe(true);
    expect(onActivity).toHaveBeenCalled();
  });

  it("does not attach when nothing is running", async () => {
    const { deps, sources } = harness({ getConversation: vi.fn(async () => detail([question, answer])) });
    const controller = createThreadController({ conversationId: "c1", deps });
    controller.subscribe(() => {});
    await vi.waitFor(() => expect(controller.getSnapshot().status).toBe("ready"));
    expect(sources).toHaveLength(0);
  });

  it("never re-attaches a run that already ended, even if the API still reports it", async () => {
    const getConversation = vi.fn().mockResolvedValue(detail([question], "r1"));
    const { deps, sources } = harness({ getConversation });
    const controller = createThreadController({ conversationId: "c1", deps });
    controller.subscribe(() => {});
    await vi.waitFor(() => expect(sources).toHaveLength(1));
    sources[0].emit("run.failed", "1-0", { code: "internal", message: "boom" });
    await vi.waitFor(() => expect(getConversation).toHaveBeenCalledTimes(2));
    expect(sources).toHaveLength(1);
    expect(controller.getSnapshot().live?.phase).toBe("failed");
  });

  it("creates a conversation for the first question, sends it and follows the run", async () => {
    const started = { run_id: "r7", user_message: msg({ id: "u7", content: "Hotel limit in London?", run_id: "r7" }) };
    const createConversation = vi.fn(async () => ({ id: "new", title: "", created_at: "", last_message_at: "" }));
    const sendMessage = vi.fn(async () => started);
    const { deps, sources } = harness({ createConversation, sendMessage });
    const onConversationCreated = vi.fn();
    const controller = createThreadController({ conversationId: null, deps, onConversationCreated });
    controller.subscribe(() => {});

    await controller.send("  Hotel limit in London?  ");
    expect(sendMessage).toHaveBeenCalledWith("new", "Hotel limit in London?");
    expect(onConversationCreated).toHaveBeenCalledWith("new");
    expect(controller.getSnapshot()).toMatchObject({ pendingUser: { id: "u7" }, live: { runId: "r7" } });
    expect(sources[0].url).toBe("/api/v1/runs/r7/events");
  });

  it("reports 409 run_in_progress and picks up the running answer instead of failing", async () => {
    const busy = new ApiError({ status: 409, title: "Busy", code: "run_in_progress" });
    const getConversation = vi.fn(async () => detail([question], "r1"));
    const { deps, sources } = harness({ getConversation, sendMessage: vi.fn(async () => Promise.reject(busy)) });
    const onBusy = vi.fn();
    const controller = createThreadController({ conversationId: "c1", deps, onBusy });
    controller.subscribe(() => {});
    await vi.waitFor(() => expect(sources).toHaveLength(1));
    sources[0].emit("run.cancelled", "1-0", { message_id: "x" }); // free the composer for the test

    await controller.send("again?");
    expect(onBusy).toHaveBeenCalledTimes(1);
  });

  it("ignores sends while a run is active and cancels the run being followed", async () => {
    const sendMessage = vi.fn();
    const { deps, sources } = harness({ getConversation: vi.fn(async () => detail([question], "r1")), sendMessage });
    const controller = createThreadController({ conversationId: "c1", deps });
    controller.subscribe(() => {});
    await vi.waitFor(() => expect(sources).toHaveLength(1));
    await controller.send("another question");
    expect(sendMessage).not.toHaveBeenCalled();
    await controller.cancel();
    expect(deps.api.cancelRun).toHaveBeenCalledWith("r1");
  });

  it("marks a missing conversation as not found", async () => {
    const missing = new ApiError({ status: 404, title: "Not found", code: "not_found" });
    const { deps } = harness({ getConversation: vi.fn(async () => Promise.reject(missing)) });
    const controller = createThreadController({ conversationId: "gone", deps });
    controller.subscribe(() => {});
    await vi.waitFor(() => expect(controller.getSnapshot().status).toBe("not_found"));
  });

  it("updates a stored message in place (feedback)", async () => {
    const { deps } = harness({ getConversation: vi.fn(async () => detail([question, answer])) });
    const controller = createThreadController({ conversationId: "c1", deps });
    controller.subscribe(() => {});
    await vi.waitFor(() => expect(controller.getSnapshot().status).toBe("ready"));
    controller.updateMessage({ ...answer, feedback: "down", feedback_comment: "wrong year" });
    expect(controller.getSnapshot().conversation?.messages[1]).toMatchObject({ feedback: "down", feedback_comment: "wrong year" });
  });

  it("a 'New chat' controller resets to blank when hidden, so returning shows an empty thread", async () => {
    const started = { run_id: "r8", user_message: msg({ id: "u8", content: "Q", run_id: "r8" }) };
    const { deps, sources } = harness({
      createConversation: vi.fn(async () => ({ id: "c8", title: "", created_at: "", last_message_at: "" })),
      sendMessage: vi.fn(async () => started),
    });
    const controller = createThreadController({ conversationId: null, deps });
    controller.subscribe(() => {});
    await controller.send("Q");
    expect(controller.getSnapshot().live?.runId).toBe("r8");

    controller.dispose(); // route hidden after navigating to /chat/c8
    expect(sources[0].closed).toBe(true);
    expect(controller.getSnapshot()).toMatchObject({ status: "ready", conversation: null, live: null, pendingUser: null });
  });

  it("keeps the agent trace of a finished run for its stored answer", async () => {
    const getConversation = vi
      .fn()
      .mockResolvedValueOnce(detail([question], "r1"))
      .mockResolvedValueOnce(detail([question, answer], null));
    const { deps, sources } = harness({ getConversation });
    const controller = createThreadController({ conversationId: "c1", deps });
    controller.subscribe(() => {});
    await vi.waitFor(() => expect(sources).toHaveLength(1));
    sources[0].emit("run.started", "1730000000000-0", {});
    sources[0].emit("plan", "1730000000001-0", { mode: "fan_out", routed_by: "planner", subtasks: [{ agent: "support", goal: "P1" }] });
    sources[0].emit("agent.step", "1730000004000-0", { agent: "support", status: "done", label: "P1", findings: 2, sources: 3, seconds: 4 });
    sources[0].emit("run.completed", "1730000019000-0", { message_id: "a1", usage: {}, abstained: false, withheld: 0 });

    await vi.waitFor(() => expect(controller.getSnapshot().live).toBeNull());
    expect(controller.getSnapshot().traces.r1).toMatchObject({ totalSeconds: 19, steps: [{ agent: "support", status: "done" }] });
  });
});

