/**
 * Framework-free "Load more" list for cursor-paginated endpoints (people, audit log).
 * Loading starts on the first subscription; pages are appended in order.
 */

export interface Page<T, C> {
  items: T[];
  next: C | null;
}

export type PagedListState<T> =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | {
      status: "ready";
      items: T[];
      hasMore: boolean;
      loadingMore: boolean;
      /** Error from the last "Load more"; the items already shown stay. */
      moreError: unknown;
    };

export interface PagedList<T> {
  getSnapshot: () => PagedListState<T>;
  subscribe: (listener: () => void) => () => void;
  loadMore: () => Promise<void>;
  reload: () => Promise<void>;
  /** Replace one item in place (e.g. after a PATCH returned the updated row). */
  updateItem: (match: (item: T) => boolean, next: T) => void;
  /** Add an item at the top (e.g. after a create). */
  prepend: (item: T) => void;
}

const LOADING_STATE = Object.freeze({ status: "loading" }) as PagedListState<never>;

export function createPagedList<T, C>(fetchPage: (cursor: C | null) => Promise<Page<T, C>>): PagedList<T> {
  let state: PagedListState<T> = LOADING_STATE;
  let cursor: C | null = null;
  let started = false;
  let generation = 0;
  const listeners = new Set<() => void>();

  const set = (next: PagedListState<T>) => {
    state = next;
    for (const listener of listeners) listener();
  };

  async function reload(): Promise<void> {
    started = true;
    const current = ++generation;
    cursor = null;
    if (state.status !== "loading") set(LOADING_STATE);
    try {
      const page = await fetchPage(null);
      if (current !== generation) return;
      cursor = page.next;
      set({ status: "ready", items: page.items, hasMore: page.next !== null, loadingMore: false, moreError: null });
    } catch (error) {
      if (current === generation) set({ status: "error", error });
    }
  }

  async function loadMore(): Promise<void> {
    if (state.status !== "ready" || !state.hasMore || state.loadingMore) return;
    const current = generation;
    set({ ...state, loadingMore: true, moreError: null });
    try {
      const page = await fetchPage(cursor);
      if (current !== generation || state.status !== "ready") return;
      cursor = page.next;
      set({ ...state, items: [...state.items, ...page.items], hasMore: page.next !== null, loadingMore: false });
    } catch (error) {
      if (current === generation && state.status === "ready") set({ ...state, loadingMore: false, moreError: error });
    }
  }

  return {
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      if (!started) void reload();
      return () => listeners.delete(listener);
    },
    loadMore,
    reload,
    updateItem(match, next) {
      if (state.status !== "ready") return;
      set({ ...state, items: state.items.map((item) => (match(item) ? next : item)) });
    },
    prepend(item) {
      if (state.status !== "ready") return;
      set({ ...state, items: [item, ...state.items] });
    },
  };
}
