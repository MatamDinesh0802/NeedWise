/**
 * Framework-free store for the current session, consumed via useSyncExternalStore.
 * Loading starts lazily on the first subscription, so no component has to call
 * setState inside an effect.
 */

import { ApiError, apiJson } from "@/lib/api";
import { parseMe, type Me } from "@/lib/session";

export type SessionState =
  | { kind: "loading" }
  | { kind: "signed_out" }
  | { kind: "signed_in"; me: Me; signingOut: boolean; signOutError: string | null }
  /** The API could not tell us (down, or returned something unexpected). */
  | { kind: "error"; message: string };

export interface SessionStore {
  getSnapshot: () => SessionState;
  subscribe: (listener: () => void) => () => void;
  /** (Re)load the session from the API. */
  load: () => Promise<void>;
  /** End the WorkMind session, then navigate to the identity provider's logout. */
  signOut: () => Promise<void>;
}

export interface SessionStoreDeps {
  fetchMe?: () => Promise<unknown>;
  postLogout?: () => Promise<unknown>;
  navigate?: (url: string) => void;
}

/** Stable object for SSR and the first client render (useSyncExternalStore needs a cached value). */
export const LOADING_STATE: SessionState = Object.freeze({ kind: "loading" });

/** Only http(s) URLs may be navigated to after logout; anything else goes home. */
export function safeRedirect(value: unknown): string {
  if (typeof value !== "string" || value === "") return "/";
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "/";
  } catch {
    return "/";
  }
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? error.title;
  return error instanceof Error ? error.message : "Unexpected error";
}

export function createSessionStore({
  fetchMe = () => apiJson<unknown>("/api/v1/me"),
  postLogout = () => apiJson<unknown>("/api/auth/logout", { method: "POST" }),
  navigate = (url) => window.location.assign(url),
}: SessionStoreDeps = {}): SessionStore {
  let state: SessionState = LOADING_STATE;
  let started = false;
  let generation = 0;
  const listeners = new Set<() => void>();

  function set(next: SessionState) {
    state = next;
    for (const listener of listeners) listener();
  }

  async function load(): Promise<void> {
    started = true;
    const current = ++generation;
    let next: SessionState;
    try {
      next = { kind: "signed_in", me: parseMe(await fetchMe()), signingOut: false, signOutError: null };
    } catch (error) {
      next =
        error instanceof ApiError && error.isUnauthenticated
          ? { kind: "signed_out" }
          : { kind: "error", message: describe(error) };
    }
    if (current === generation) set(next); // Ignore results superseded by a newer load.
  }

  async function signOut(): Promise<void> {
    if (state.kind !== "signed_in" || state.signingOut) return;
    const me = state.me;
    set({ kind: "signed_in", me, signingOut: true, signOutError: null });
    try {
      const body = await postLogout();
      const redirect = safeRedirect((body as { redirect_url?: unknown } | undefined)?.redirect_url);
      generation++; // Any in-flight load must not resurrect the old session.
      set({ kind: "signed_out" });
      navigate(redirect);
    } catch (error) {
      if (error instanceof ApiError && error.isUnauthenticated) {
        generation++;
        set({ kind: "signed_out" }); // Session had already ended.
        return;
      }
      set({ kind: "signed_in", me, signingOut: false, signOutError: describe(error) });
    }
  }

  return {
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      if (!started) void load();
      return () => listeners.delete(listener);
    },
    load,
    signOut,
  };
}

let browserStore: SessionStore | null = null;

/** The app-wide store (one per browser tab). */
export function getSessionStore(): SessionStore {
  browserStore ??= createSessionStore();
  return browserStore;
}
