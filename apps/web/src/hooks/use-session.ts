"use client";

import { useSyncExternalStore } from "react";

import { getSessionStore, LOADING_STATE, type SessionState } from "@/lib/session-store";

const getServerSnapshot = (): SessionState => LOADING_STATE;

/** Current session. Starts as "loading" on the server and during hydration. */
export function useSession() {
  const store = getSessionStore();
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, getServerSnapshot);
  return { state, signOut: store.signOut, switchAccount: store.switchAccount, reload: store.load };
}
