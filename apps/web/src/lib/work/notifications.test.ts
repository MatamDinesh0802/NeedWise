import { describe, expect, it, vi } from "vitest";

import type { Notification } from "@/lib/work/api";

import { badgeLabel, bellLabel, createNotificationsStore, notificationTone, unreadCount } from "./notifications";

const note = (id: string, read = false): Notification => ({
  id,
  kind: "approval.requested",
  title: `Approval needed: ${id}`,
  body: "",
  link: `/approvals/${id}`,
  read_at: read ? "2026-11-04T09:00:00Z" : null,
  created_at: "2026-11-04T08:00:00Z",
});

describe("badge", () => {
  it("counts unread notifications", () => {
    expect(unreadCount([note("a"), note("b", true), note("c")])).toBe(2);
    expect(unreadCount([])).toBe(0);
  });

  it.each([
    [0, null],
    [-1, null],
    [1, "1"],
    [9, "9"],
    [10, "9+"],
    [250, "9+"],
  ])("badgeLabel(%d) → %j", (count, label) => {
    expect(badgeLabel(count)).toBe(label);
  });

  it("names the bell with the unread count for screen readers", () => {
    expect(bellLabel(0)).toBe("Notifications");
    expect(bellLabel(3)).toBe("Notifications, 3 unread");
  });

  it("gives approval requests, problems and tasks their own tone", () => {
    expect(notificationTone("approval.requested")).toBe("approval");
    expect(notificationTone("approval.executed")).toBe("done");
    expect(notificationTone("meeting.ready")).toBe("done");
    expect(notificationTone("approval.rejected")).toBe("problem");
    expect(notificationTone("workflow.failed")).toBe("problem");
    expect(notificationTone("task.assigned")).toBe("task");
    expect(notificationTone("something.else")).toBe("info");
  });
});

function harness(lists: Notification[][]) {
  const list = vi.fn(async () => lists.shift() ?? []);
  const markRead = vi.fn(async () => undefined);
  let tick: (() => void) | null = null;
  let focus: (() => void) | null = null;
  let visible = true;
  const cleared = vi.fn();
  const removeFocus = vi.fn();
  const store = createNotificationsStore({
    list,
    markRead,
    intervalMs: 30_000,
    setInterval: (fn) => {
      tick = fn;
      return 42;
    },
    clearInterval: cleared,
    onFocus: (fn) => {
      focus = fn;
      return removeFocus;
    },
    isVisible: () => visible,
    now: () => new Date("2026-11-04T10:00:00Z"),
  });
  return {
    store,
    list,
    markRead,
    cleared,
    removeFocus,
    tick: () => tick?.(),
    focus: () => focus?.(),
    hide: () => {
      visible = false;
    },
  };
}

describe("notifications store", () => {
  it("loads on the first subscriber, polls while visible, refetches on focus and stops when nobody listens", async () => {
    const h = harness([[note("a")], [note("a"), note("b")], [note("a")]]);
    const unsubscribe = h.store.subscribe(() => {});
    await vi.waitFor(() => expect(h.store.getSnapshot().status).toBe("ready"));
    expect(unreadCount(h.store.getSnapshot().items)).toBe(1);

    h.tick();
    await vi.waitFor(() => expect(h.store.getSnapshot().items).toHaveLength(2));

    h.hide();
    h.tick(); // hidden tab: no request
    expect(h.list).toHaveBeenCalledTimes(2);

    h.focus();
    await vi.waitFor(() => expect(h.list).toHaveBeenCalledTimes(3));

    unsubscribe();
    expect(h.cleared).toHaveBeenCalledWith(42);
    expect(h.removeFocus).toHaveBeenCalled();
  });

  it("marks read optimistically, then confirms with the server", async () => {
    const h = harness([[note("a"), note("b")], [note("a", true), note("b")]]);
    h.store.subscribe(() => {});
    await vi.waitFor(() => expect(h.store.getSnapshot().items).toHaveLength(2));

    const marking = h.store.markRead(["a"]);
    expect(h.store.getSnapshot().items.map((n) => n.read_at !== null)).toEqual([true, false]); // before the request finishes
    await marking;
    expect(h.markRead).toHaveBeenCalledWith(["a"]);
  });

  it("marks everything read when no ids are given", async () => {
    const h = harness([[note("a"), note("b")], []]);
    h.store.subscribe(() => {});
    await vi.waitFor(() => expect(h.store.getSnapshot().items).toHaveLength(2));
    void h.store.markRead();
    expect(unreadCount(h.store.getSnapshot().items)).toBe(0);
    expect(h.markRead).toHaveBeenCalledWith(undefined);
  });

  it("keeps the last list when a poll fails", async () => {
    const list = vi.fn().mockResolvedValueOnce([note("a")]).mockRejectedValueOnce(new Error("offline"));
    let tick: () => void = () => {};
    const store = createNotificationsStore({
      list,
      markRead: async () => undefined,
      setInterval: (fn) => {
        tick = fn;
        return 1;
      },
      clearInterval: () => {},
      onFocus: () => () => {},
      isVisible: () => true,
    });
    store.subscribe(() => {});
    await vi.waitFor(() => expect(store.getSnapshot().status).toBe("ready"));
    tick();
    await vi.waitFor(() => expect(store.getSnapshot().error).toBeInstanceOf(Error));
    expect(store.getSnapshot()).toMatchObject({ status: "ready", items: [{ id: "a" }] });
  });
});
