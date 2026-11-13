/** "View as" demo accounts (seeded Acme Robotics users; password: workmind-demo). */

export interface DemoAccount {
  email: string;
  name: string;
  role: string;
}

export const DEMO_ACCOUNTS: readonly DemoAccount[] = [
  { email: "alice@acme.test", name: "Alice", role: "HR manager" },
  { email: "bob@acme.test", name: "Bob", role: "Engineer" },
  { email: "carol@acme.test", name: "Carol", role: "Finance analyst" },
  { email: "dan@acme.test", name: "Dan", role: "Sales" },
  { email: "erin@acme.test", name: "Erin", role: "IT admin" },
  { email: "priya.raman@acme.test", name: "Priya", role: "CEO" },
  { email: "hannah.schmidt@acme.test", name: "Hannah", role: "CFO" },
  { email: "olivia.grant@acme.test", name: "Olivia", role: "Head of operations" },
  { email: "mallory@acme.test", name: "Mallory", role: "Contractor (guest)" },
];

/** Sign-in URL that pre-fills the account; the API adds prompt=login so Keycloak asks again. */
export function viewAsLoginUrl(email: string, returnTo = "/chat"): string {
  return `/api/auth/login?login_hint=${encodeURIComponent(email)}&return_to=${encodeURIComponent(returnTo)}`;
}

/**
 * Switching accounts must end the Keycloak SSO session too: with a live session,
 * prompt=login re-authenticates *that* user and ignores login_hint. So we follow the
 * logout's end-session redirect (which lands on "/") and resume the switch from there.
 */
export const PENDING_VIEW_AS_KEY = "workmind:view-as";
const PENDING_MAX_AGE_MS = 2 * 60 * 1000;

export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function rememberViewAs(storage: KeyValueStorage, email: string, now = Date.now()): void {
  storage.setItem(PENDING_VIEW_AS_KEY, JSON.stringify({ email, at: now }));
}

/** The pending switch, if any and recent; consumed (removed) either way. */
export function takePendingViewAs(storage: KeyValueStorage, now = Date.now()): string | null {
  const raw = storage.getItem(PENDING_VIEW_AS_KEY);
  if (raw === null) return null;
  storage.removeItem(PENDING_VIEW_AS_KEY);
  try {
    const { email, at } = JSON.parse(raw) as { email?: unknown; at?: unknown };
    if (typeof email !== "string" || typeof at !== "number" || now - at > PENDING_MAX_AGE_MS) return null;
    return DEMO_ACCOUNTS.some((a) => a.email === email) ? email : null;
  } catch {
    return null;
  }
}

export interface RuntimeConfig {
  demo: boolean;
}

export async function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  const response = await fetch("/runtime-config", { cache: "no-store" });
  if (!response.ok) return { demo: false };
  const body = (await response.json()) as Partial<RuntimeConfig>;
  return { demo: body.demo === true };
}
