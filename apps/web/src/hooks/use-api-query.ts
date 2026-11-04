"use client";

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";

import { getQueryCache, LOADING, type QueryState } from "@/lib/stores/query-cache";

const getServerSnapshot = () => LOADING;

/**
 * Reads `key` from the shared query cache, fetching with `fetcher` on first use.
 * The key must encode every input of the fetcher (e.g. "admin/users/search?q=al").
 * Pass `null` as the key to skip fetching.
 */
export function useApiQuery<T>(key: string | null, fetcher: () => Promise<T>) {
  const cache = getQueryCache();
  const latestFetcher = useRef(fetcher);
  useEffect(() => {
    latestFetcher.current = fetcher;
  });

  // Stable per key, so React never resubscribes (and refetches) on every render.
  const subscribe = useCallback(
    (listener: () => void) =>
      key === null ? () => {} : cache.subscribe(key, () => latestFetcher.current(), listener),
    [cache, key],
  );
  const getSnapshot = useCallback(
    () => (key === null ? LOADING : cache.getSnapshot<T>(key)),
    [cache, key],
  );

  const state = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot) as QueryState<T>;
  const refetch = useCallback(() => (key === null ? Promise.resolve() : cache.refetch(key)), [cache, key]);
  return { state, refetch };
}

/** Invalidate cached queries after a mutation. */
export function invalidateQueries(prefix: string): Promise<void> {
  return getQueryCache().invalidate(prefix);
}
