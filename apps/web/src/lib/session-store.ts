/**
 * Framework-free store for the current session, consumed via useSyncExternalStore.
 * Loading starts lazily on the first subscription, so no component has to call
 * setState inside an effect.
 */

import { ApiError, apiJson } from "@/lib/api";
import { rememberViewAs, viewAsLoginUrl, type KeyValueStorage } from "@/lib/demo";
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
  /** End the NeedWise session, then navigate to the identity provider's logout. */
  signOut: () => Promise<void>;
  /**
   * Demo "view as": end this session, then sign in again with `email` pre-filled.
   * The API asks Keycloak for prompt=login, so the other account's password is required.
   */
  switchAccount: (email: string) => Promise<void>;
}

export interface SessionStoreDeps {
  fetchMe?: () => Promise<unknown>;
  postLogout?: () => Promise<unknown>;
  navigate?: (url: string) => void;
  storage?: () => KeyValueStorage | null;
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
  storage = () => (typeof window === "undefined" ? null : window.sessionStorage),
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

  async function switchAccount(email: string): Promise<void> {
    if (state.kind === "signed_in") set({ ...state, signingOut: true, signOutError: null });
    let endSession = "/";
    try {
      const body = await postLogout();
      endSession = safeRedirect((body as { redirect_url?: unknown } | undefined)?.redirect_url);
    } catch {
      // Already signed out: nothing to end; go straight to the hinted sign-in.
    }
    generation++;
    set({ kind: "signed_out" });
    const store = storage();
    if (endSession !== "/" && store) {
      // End the identity provider session first; ViewAsResume continues on "/".
      rememberViewAs(store, email);
      navigate(endSession);
    } else {
      navigate(viewAsLoginUrl(email));
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
    switchAccount,
  };
}

let browserStore: SessionStore | null = null;

/** The app-wide store (one per browser tab). */
export function getSessionStore(): SessionStore {
  browserStore ??= createSessionStore();
  return browserStore;
}
