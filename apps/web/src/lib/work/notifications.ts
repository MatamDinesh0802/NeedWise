/**
 * The notifications bell, framework-free (consumed via useSyncExternalStore): polls every 30 s
 * while someone is subscribed and the tab is visible, refetches when the window regains focus,
 * and marks notifications read optimistically. Timers and events are injected for tests.
 */

import { workApi, type Notification } from "@/lib/work/api";

export const NOTIFICATIONS_POLL_MS = 30_000;

export function unreadCount(items: readonly Pick<Notification, "read_at">[]): number {
  return items.filter((n) => n.read_at === null).length;
}

/** The badge text: nothing at 0, then "1"…"9", then "9+". */
export function badgeLabel(count: number): string | null {
  if (!Number.isFinite(count) || count <= 0) return null;
  return count > 9 ? "9+" : String(Math.floor(count));
}

/** Accessible name for the bell button. */
export function bellLabel(count: number): string {
  return count > 0 ? `Notifications, ${count} unread` : "Notifications";
}

export type NotificationTone = "approval" | "done" | "problem" | "task" | "info";

export function notificationTone(kind: string): NotificationTone {
  if (kind === "approval.requested") return "approval";
  if (kind === "approval.executed" || kind === "workflow.succeeded") return "done";
  if (/\.(failed|rejected|expired|cancelled)$/.test(kind)) return "problem";
  if (kind.startsWith("task.")) return "task";
  return "info";
}

export interface NotificationsState {
  status: "idle" | "loading" | "ready" | "error";
  items: Notification[];
  error: unknown;
}

export interface NotificationsDeps {
  list: () => Promise<Notification[]>;
  markRead: (ids?: string[]) => Promise<void>;
  intervalMs?: number;
  setInterval?: (fn: () => void, ms: number) => unknown;
  clearInterval?: (handle: unknown) => void;
  /** Calls `fn` when the window regains focus or the tab becomes visible; returns a remover. */
  onFocus?: (fn: () => void) => () => void;
  isVisible?: () => boolean;
  now?: () => Date;
}

export interface NotificationsStore {
  getSnapshot: () => NotificationsState;
  subscribe: (listener: () => void) => () => void;
  refresh: () => Promise<void>;
  /** Marks the given notifications read, or all of them. */
  markRead: (ids?: string[]) => Promise<void>;
}

function browserFocus(fn: () => void): () => void {
  const onVisible = () => {
    if (document.visibilityState === "visible") fn();
  };
  window.addEventListener("focus", fn);
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    window.removeEventListener("focus", fn);
    document.removeEventListener("visibilitychange", onVisible);
  };
}

export function createNotificationsStore(deps: NotificationsDeps): NotificationsStore {
  const {
    list,
    markRead: sendRead,
    intervalMs = NOTIFICATIONS_POLL_MS,
    setInterval: startTimer = (fn, ms) => globalThis.setInterval(fn, ms),
    clearInterval: stopTimer = (handle) => globalThis.clearInterval(handle as ReturnType<typeof globalThis.setInterval>),
    onFocus = browserFocus,
    isVisible = () => typeof document === "undefined" || document.visibilityState === "visible",
    now = () => new Date(),
  } = deps;

  let state: NotificationsState = { status: "idle", items: [], error: null };
  const listeners = new Set<() => void>();
  let timer: unknown = null;
  let removeFocus: (() => void) | null = null;
  let inFlight: Promise<void> | null = null;
  let generation = 0;

  const set = (patch: Partial<NotificationsState>) => {
    state = { ...state, ...patch };
    for (const listener of listeners) listener();
  };

  function refresh(): Promise<void> {
    if (inFlight) return inFlight;
    const current = generation;
    if (state.status === "idle") set({ status: "loading" });
    inFlight = list()
      .then((items) => {
        if (current === generation) set({ status: "ready", items, error: null });
      })
      .catch((error: unknown) => {
        // Keep showing what we had; the bell shouldn't flash errors on a flaky network.
        if (current === generation) set({ status: state.items.length > 0 ? "ready" : "error", error });
      })
      .finally(() => {
        inFlight = null;
      });
    return inFlight;
  }

  function start() {
    void refresh();
    timer = startTimer(() => {
      if (isVisible()) void refresh();
    }, intervalMs);
    removeFocus = onFocus(() => void refresh());
  }

  function stop() {
    if (timer !== null) stopTimer(timer);
    timer = null;
    removeFocus?.();
    removeFocus = null;
  }

  return {
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1) start();
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) stop();
      };
    },
    refresh,
    async markRead(ids) {
      const readAt = now().toISOString();
      const targets = ids ? new Set(ids) : null;
      generation++; // a poll that started before this must not undo it
      set({
        items: state.items.map((n) => (n.read_at === null && (!targets || targets.has(n.id)) ? { ...n, read_at: readAt } : n)),
      });
      try {
        await sendRead(ids);
      } finally {
        void refresh();
      }
    },
  };
}

let shared: NotificationsStore | null = null;

/** The app-wide store (one poller however many bells render). */
export function getNotificationsStore(): NotificationsStore {
  shared ??= createNotificationsStore({ list: workApi.listNotifications, markRead: workApi.markRead });
  return shared;
}
