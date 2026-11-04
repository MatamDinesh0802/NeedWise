/**
 * A tiny framework-free query cache (think "mini SWR") for useSyncExternalStore.
 * Entries are keyed by string; fetching starts on the first subscription; mutations
 * call `invalidate(prefix)` to refetch everything under that prefix.
 */

export type QueryState<T> =
  | { status: "loading" }
  | { status: "success"; data: T; refreshing: boolean }
  | { status: "error"; error: unknown; refreshing: boolean };

interface Entry {
  state: QueryState<unknown>;
  fetcher: () => Promise<unknown>;
  listeners: Set<() => void>;
  generation: number;
}

export const LOADING: QueryState<never> = Object.freeze({ status: "loading" });

export interface QueryCache {
  getSnapshot: <T>(key: string) => QueryState<T>;
  subscribe: (key: string, fetcher: () => Promise<unknown>, listener: () => void) => () => void;
  refetch: (key: string) => Promise<void>;
  /** Refetch active entries whose key starts with `prefix`; drop inactive ones. */
  invalidate: (prefix: string) => Promise<void>;
  /** Replace cached data (e.g. after a mutation returned the new value). */
  setData: <T>(key: string, update: (current: T | undefined) => T) => void;
}

export function createQueryCache(): QueryCache {
  const entries = new Map<string, Entry>();

  function notify(entry: Entry) {
    for (const listener of entry.listeners) listener();
  }

  async function run(key: string, entry: Entry): Promise<void> {
    const generation = ++entry.generation;
    const previous = entry.state;
    if (previous.status !== "loading") {
      entry.state = { ...previous, refreshing: true };
      notify(entry);
    }
    let next: QueryState<unknown>;
    try {
      next = { status: "success", data: await entry.fetcher(), refreshing: false };
    } catch (error) {
      next = { status: "error", error, refreshing: false };
    }
    // Only the latest request for this key may write, and only if the entry still exists.
    if (entries.get(key) === entry && entry.generation === generation) {
      entry.state = next;
      notify(entry);
    }
  }

  return {
    getSnapshot: <T,>(key: string) => (entries.get(key)?.state ?? LOADING) as QueryState<T>,

    subscribe(key, fetcher, listener) {
      let entry = entries.get(key);
      const isNew = entry === undefined;
      if (!entry) {
        entry = { state: LOADING, fetcher, listeners: new Set(), generation: 0 };
        entries.set(key, entry);
      }
      entry.fetcher = fetcher;
      entry.listeners.add(listener);
      // New entries fetch now; entries that went idle refetch so returning views are fresh.
      if (isNew || (entry.listeners.size === 1 && entry.state.status !== "loading")) void run(key, entry);
      const current = entry;
      return () => {
        current.listeners.delete(listener);
      };
    },

    refetch(key) {
      const entry = entries.get(key);
      return entry ? run(key, entry) : Promise.resolve();
    },

    async invalidate(prefix) {
      const runs: Promise<void>[] = [];
      for (const [key, entry] of entries) {
        if (!key.startsWith(prefix)) continue;
        if (entry.listeners.size > 0) runs.push(run(key, entry));
        else entries.delete(key);
      }
      await Promise.all(runs);
    },

    setData(key, update) {
      const entry = entries.get(key);
      if (!entry) return;
      const current = entry.state.status === "success" ? (entry.state.data as never) : undefined;
      entry.generation++; // a slower in-flight fetch must not overwrite this
      entry.state = { status: "success", data: update(current), refreshing: false };
      notify(entry);
    },
  };
}

let browserCache: QueryCache | null = null;

/** The app-wide cache (one per browser tab). */
export function getQueryCache(): QueryCache {
  browserCache ??= createQueryCache();
  return browserCache;
}
