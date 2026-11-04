/**
 * Friendly messages for failed sign-ins. The API redirects to `/?auth_error=<code>`.
 * Framework-free so the mapping can be unit-tested.
 */

export const AUTH_ERROR_PARAM = "auth_error";

export interface AuthErrorMessage {
  /** The code, if it looks safe to show (for support requests). */
  code: string | null;
  title: string;
  description: string;
}

const MESSAGES: Record<string, Omit<AuthErrorMessage, "code">> = {
  login_expired: {
    title: "Your sign-in timed out",
    description: "The sign-in took too long and expired. Please sign in again.",
  },
  login_cancelled: {
    title: "Sign-in cancelled",
    description: "You cancelled signing in. Sign in again whenever you're ready.",
  },
  login_unavailable: {
    title: "Sign-in is temporarily unavailable",
    description: "We couldn't reach the sign-in service. Please try again in a moment.",
  },
  login_failed: {
    title: "We couldn't sign you in",
    description:
      "Something went wrong while completing your sign-in. Please try again; if it keeps happening, contact your administrator.",
  },
  no_organization: {
    title: "No organization for this account",
    description:
      "Your account isn't a member of any organization in this workspace yet. Ask your administrator to invite you.",
  },
  account_disabled: {
    title: "Your account is disabled",
    description: "An administrator has disabled this account. Contact them if you think this is a mistake.",
  },
  email_not_verified: {
    title: "Verify your email address",
    description: "Confirm your email address using the link we sent you, then sign in again.",
  },
  account_conflict: {
    title: "This sign-in matches another account",
    description:
      "The identity you used is linked to a different account in this workspace. Contact your administrator to resolve it.",
  },
};

const FALLBACK: Omit<AuthErrorMessage, "code"> = {
  title: "Sign-in problem",
  description: "We couldn't complete your sign-in. Please try again.",
};

const SAFE_CODE = /^[a-z0-9_]{1,64}$/;

export const KNOWN_AUTH_ERROR_CODES = Object.keys(MESSAGES);

/** Maps an `auth_error` code to a message. Returns null when there is no error. */
export function authErrorMessage(code: string | null | undefined): AuthErrorMessage | null {
  const trimmed = code?.trim();
  if (!trimmed) return null;
  const known = Object.hasOwn(MESSAGES, trimmed) ? MESSAGES[trimmed] : FALLBACK;
  return { ...known, code: SAFE_CODE.test(trimmed) ? trimmed : null };
}

/** Reads the auth error code from a query string (with or without the leading "?"). */
export function readAuthError(search: string): string | null {
  return new URLSearchParams(search).get(AUTH_ERROR_PARAM);
}

/** Returns path + query + hash of `href` with the auth error flag removed. */
export function stripAuthErrorParam(href: string): string {
  const url = new URL(href, "http://placeholder.invalid");
  url.searchParams.delete(AUTH_ERROR_PARAM);
  return `${url.pathname}${url.search}${url.hash}`;
}
