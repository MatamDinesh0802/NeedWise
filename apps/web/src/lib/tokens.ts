/**
 * Personal access tokens (/api/v1/me/tokens): types, API calls, and the show-once
 * creation flow. The plain token exists only in memory, only between "created" and
 * "Done"; it is never put in the list cache or anywhere else.
 */

import { apiJson } from "@/lib/api";
import { errorMessage, type ErrorMessage } from "@/lib/error-messages";

export interface AccessToken {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface CreatedAccessToken extends AccessToken {
  /** The plain token (`wm_pat_…`). Returned by the API exactly once. */
  token: string;
}

export interface TokenScope {
  value: string;
  label: string;
  description: string;
  /** False while the capability behind the scope hasn't shipped yet. */
  active: boolean;
}

export const TOKEN_SCOPES: readonly TokenScope[] = [
  {
    value: "profile:read",
    label: "Read profile",
    description: "Read your name, organization, departments and groups.",
    active: true,
  },
  {
    value: "knowledge:read",
    label: "Search knowledge",
    description: "Search documents you have access to.",
    active: false,
  },
  {
    value: "assistant:ask",
    label: "Ask the assistant",
    description: "Ask questions and get cited answers.",
    active: false,
  },
];

export const LATER_PHASE_NOTE = "Takes effect in a later phase.";

export const EXPIRY_OPTIONS = [7, 30, 90, 180, 365] as const;
export const DEFAULT_EXPIRY_DAYS = 90;
export const TOKEN_NAME_MAX = 100;

export const tokensKey = "me/tokens";

export const tokensApi = {
  list: () => apiJson<{ items: AccessToken[] }>("/api/v1/me/tokens"),
  create: (body: { name: string; scopes: string[]; expires_in_days: number }) =>
    apiJson<CreatedAccessToken>("/api/v1/me/tokens", { method: "POST", json: body }),
  revoke: (tokenId: string) =>
    apiJson<void>(`/api/v1/me/tokens/${encodeURIComponent(tokenId)}`, { method: "DELETE" }),
};

export type TokenStatus = "active" | "expired" | "revoked";

export function tokenStatus(token: AccessToken, now: Date = new Date()): TokenStatus {
  if (token.revoked_at) return "revoked";
  if (new Date(token.expires_at).getTime() <= now.getTime()) return "expired";
  return "active";
}

export interface TokenFormInput {
  name: string;
  scopes: string[];
  expiresInDays: number;
}

/** Client-side checks; returns field → message. Empty object means valid. */
export function validateTokenForm(input: TokenFormInput): Partial<Record<keyof TokenFormInput, string>> {
  const errors: Partial<Record<keyof TokenFormInput, string>> = {};
  const name = input.name.trim();
  if (!name) errors.name = "Give the token a name, e.g. the tool that will use it.";
  else if (name.length > TOKEN_NAME_MAX) errors.name = `Use at most ${TOKEN_NAME_MAX} characters.`;
  if (input.scopes.length === 0) errors.scopes = "Choose at least one scope.";
  if (!Number.isInteger(input.expiresInDays) || input.expiresInDays < 1 || input.expiresInDays > 365) {
    errors.expiresInDays = "Choose an expiry between 1 and 365 days.";
  }
  return errors;
}

/** Drops the plain token, so only the metadata can ever be stored or listed. */
export function toTokenRecord(created: CreatedAccessToken): AccessToken {
  const { token: _plain, ...record } = created;
  void _plain;
  return record;
}

// --- Show-once creation flow -----------------------------------------------------------

export type CreateTokenState =
  | { step: "form"; error: ErrorMessage | null; fieldErrors: Partial<Record<keyof TokenFormInput, string>> }
  | { step: "creating" }
  | { step: "reveal"; token: string; record: AccessToken; copy: "idle" | "copied" | "failed" };

export interface CreateTokenFlow {
  getSnapshot: () => CreateTokenState;
  subscribe: (listener: () => void) => () => void;
  submit: (input: TokenFormInput) => Promise<void>;
  copy: () => Promise<void>;
  /** "Done": forget the plain token for good and go back to an empty form. */
  finish: () => void;
}

const INITIAL: CreateTokenState = Object.freeze({ step: "form", error: null, fieldErrors: {} });

export function createTokenFlow({
  create = tokensApi.create,
  writeClipboard = (text: string) => navigator.clipboard.writeText(text),
  onCreated,
}: {
  create?: typeof tokensApi.create;
  writeClipboard?: (text: string) => Promise<void>;
  /** Called with the token metadata (never the plain token), e.g. to refresh the list. */
  onCreated?: (record: AccessToken) => void;
} = {}): CreateTokenFlow {
  let state: CreateTokenState = INITIAL;
  const listeners = new Set<() => void>();
  const set = (next: CreateTokenState) => {
    state = next;
    for (const listener of listeners) listener();
  };

  return {
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async submit(input) {
      if (state.step !== "form") return;
      const fieldErrors = validateTokenForm(input);
      if (Object.keys(fieldErrors).length > 0) {
        set({ step: "form", error: null, fieldErrors });
        return;
      }
      set({ step: "creating" });
      try {
        const created = await create({
          name: input.name.trim(),
          scopes: input.scopes,
          expires_in_days: input.expiresInDays,
        });
        const record = toTokenRecord(created);
        set({ step: "reveal", token: created.token, record, copy: "idle" });
        onCreated?.(record);
      } catch (error) {
        set({ step: "form", error: errorMessage(error), fieldErrors: {} });
      }
    },
    async copy() {
      if (state.step !== "reveal") return;
      const token = state.token;
      let result: "copied" | "failed";
      try {
        await writeClipboard(token);
        result = "copied";
      } catch {
        result = "failed";
      }
      // Ignore the result if the user already pressed "Done" meanwhile.
      if (state.step === "reveal" && state.token === token) set({ ...state, copy: result });
    },
    finish() {
      set(INITIAL);
    },
  };
}
