import { describe, expect, it } from "vitest";

import { DEMO_ACCOUNTS, PENDING_VIEW_AS_KEY, rememberViewAs, takePendingViewAs, viewAsLoginUrl } from "./demo";

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

describe("view as", () => {
  it("lists the seeded demo accounts with roles", () => {
    expect(DEMO_ACCOUNTS.map((a) => a.email)).toEqual([
      "alice@acme.test",
      "bob@acme.test",
      "carol@acme.test",
      "dan@acme.test",
      "erin@acme.test",
      "priya.raman@acme.test",
      "hannah.schmidt@acme.test",
      "olivia.grant@acme.test",
      "mallory@acme.test",
    ]);
    expect(DEMO_ACCOUNTS.find((a) => a.email === "mallory@acme.test")?.role).toMatch(/guest/);
  });

  it("builds the hinted sign-in URL back to /chat", () => {
    expect(viewAsLoginUrl("priya.raman@acme.test")).toBe(
      "/api/auth/login?login_hint=priya.raman%40acme.test&return_to=%2Fchat",
    );
  });

  it("hands the pending switch over exactly once", () => {
    const storage = memoryStorage();
    rememberViewAs(storage, "bob@acme.test", 1_000);
    expect(takePendingViewAs(storage, 2_000)).toBe("bob@acme.test");
    expect(takePendingViewAs(storage, 2_000)).toBeNull();
    expect(storage.data.has(PENDING_VIEW_AS_KEY)).toBe(false);
  });

  it("ignores stale, unknown or corrupt entries", () => {
    const storage = memoryStorage();
    rememberViewAs(storage, "bob@acme.test", 0);
    expect(takePendingViewAs(storage, 3 * 60 * 1000)).toBeNull(); // older than 2 minutes
    rememberViewAs(storage, "attacker@evil.example", 0);
    expect(takePendingViewAs(storage, 1)).toBeNull();
    storage.setItem(PENDING_VIEW_AS_KEY, "{oops");
    expect(takePendingViewAs(storage)).toBeNull();
  });
});
