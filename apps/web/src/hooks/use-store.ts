"use client";

import { useSyncExternalStore } from "react";

interface ExternalStore<S> {
  getSnapshot: () => S;
  subscribe: (listener: () => void) => () => void;
}

/** Subscribes to any of our framework-free stores (paged lists, flows, …). */
export function useStore<S>(store: ExternalStore<S>): S {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}
