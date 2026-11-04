/**
 * Framework-free "flash" store for the `?auth_error=` redirect from the API.
 * On first subscription (React's commit phase, never during render) it reads the
 * code once, then removes it from the URL so a reload doesn't show it again.
 */

import { authErrorMessage, readAuthError, stripAuthErrorParam, type AuthErrorMessage } from "@/lib/auth-errors";

export interface AuthErrorStore {
  getSnapshot: () => AuthErrorMessage | null;
  subscribe: (listener: () => void) => () => void;
  dismiss: () => void;
}

export interface AuthErrorStoreDeps {
  /** Current location (path + query + hash). */
  getLocation?: () => { href: string; search: string };
  /** Replaces the URL without navigating. */
  replaceUrl?: (pathWithQuery: string) => void;
}

export function createAuthErrorStore({
  getLocation = () => window.location,
  // Pass the existing history state through so the Next.js router keeps its own entry state.
  replaceUrl = (url) => window.history.replaceState(window.history.state, "", url),
}: AuthErrorStoreDeps = {}): AuthErrorStore {
  let message: AuthErrorMessage | null = null;
  let consumed = false;
  const listeners = new Set<() => void>();

  function notify() {
    for (const listener of listeners) listener();
  }

  function consume() {
    consumed = true;
    const location = getLocation();
    const code = readAuthError(location.search);
    if (code === null) return;
    message = authErrorMessage(code);
    replaceUrl(stripAuthErrorParam(location.href));
    notify();
  }

  return {
    getSnapshot: () => message,
    subscribe(listener) {
      listeners.add(listener);
      if (!consumed) consume();
      return () => listeners.delete(listener);
    },
    dismiss() {
      if (message === null) return;
      message = null;
      notify();
    },
  };
}

let browserStore: AuthErrorStore | null = null;

export function getAuthErrorStore(): AuthErrorStore {
  browserStore ??= createAuthErrorStore();
  return browserStore;
}
