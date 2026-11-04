import { describe, expect, it, vi } from "vitest";

import { createRunStream, type EventSourceLike } from "./run-stream";

class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, ((event: { data: string; lastEventId?: string }) => void)[]>();
  onerror: ((event: unknown) => void) | null = null;
  readyState = 1;
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: (event: { data: string; lastEventId?: string }) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emit(type: string, id: string, data: unknown) {
    for (const listener of this.listeners.get(type) ?? []) listener({ data: JSON.stringify(data), lastEventId: id });
  }
}

function setup() {
  FakeEventSource.instances = [];
  const onTerminal = vi.fn();
  const stream = createRunStream("run 1", { createEventSource: (url) => new FakeEventSource(url), onTerminal });
  return { stream, onTerminal, es: () => FakeEventSource.instances.at(-1)! };
}

describe("createRunStream", () => {
  it("opens the SSE stream on first subscribe, at the run's events URL", () => {
    const { stream, es } = setup();
    expect(FakeEventSource.instances).toHaveLength(0);
    stream.subscribe(() => {});
    stream.subscribe(() => {});
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(es().url).toBe("/api/v1/runs/run%201/events");
  });

  it("reduces events, closes the EventSource after the terminal event and reports once", () => {
    const { stream, onTerminal, es } = setup();
    const listener = vi.fn();
    stream.subscribe(listener);
    es().emit("run.started", "1-0", {});
    es().emit("token", "2-0", { text: "Hello" });
    es().emit("run.completed", "3-0", { message_id: "m1", usage: {}, abstained: false, withheld: 0 });

    expect(stream.getSnapshot()).toMatchObject({ phase: "completed", text: "Hello", messageId: "m1" });
    expect(es().closed).toBe(true); // otherwise the browser reconnects forever
    expect(onTerminal).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalled();
  });

  it("survives a browser reconnect that replays already-seen events", () => {
    const { stream, es } = setup();
    stream.subscribe(() => {});
    es().emit("token", "1-0", { text: "A" });
    es().emit("token", "2-0", { text: "B" });
    es().onerror?.({}); // CONNECTING: the browser retries with Last-Event-ID
    es().emit("token", "2-0", { text: "B" }); // a replayed frame
    es().emit("token", "3-0", { text: "C" });
    expect(stream.getSnapshot().text).toBe("ABC");
  });

  it("reports a lost stream when the browser gives up (e.g. expired run)", () => {
    const { stream, onTerminal, es } = setup();
    stream.subscribe(() => {});
    es().readyState = 2;
    es().onerror?.({});
    expect(stream.getSnapshot()).toMatchObject({ phase: "failed", error: { code: "stream_lost" } });
    expect(onTerminal).toHaveBeenCalledTimes(1);
  });

  it("ignores malformed frames and stops after close()", () => {
    const { stream, onTerminal, es } = setup();
    stream.subscribe(() => {});
    for (const listener of es().listeners.get("token") ?? []) listener({ data: "{not json", lastEventId: "1-0" });
    expect(stream.getSnapshot().text).toBe("");
    stream.close();
    expect(es().closed).toBe(true);
    expect(onTerminal).not.toHaveBeenCalled();
  });
});
