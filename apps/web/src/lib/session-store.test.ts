import { describe, expect, it, vi } from "vitest";

import { alice } from "@/test/fixtures";

import { ApiError } from "./api";
import { createSessionStore, safeRedirect, type SessionState } from "./session-store";

const unauthenticated = () =>
  new ApiError({ status: 401, title: "Not signed in", code: "unauthenticated", detail: "Sign in to continue." });

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function track(store: ReturnType<typeof createSessionStore>) {
  const seen: SessionState["kind"][] = [];
  const unsubscribe = store.subscribe(() => seen.push(store.getSnapshot().kind));
  return { seen, unsubscribe };
}

describe("session store", () => {
  it("starts loading and fetches /me on the first subscription only", async () => {
    const fetchMe = vi.fn(async () => alice);
    const store = createSessionStore({ fetchMe });
    expect(store.getSnapshot().kind).toBe("loading");
    expect(fetchMe).not.toHaveBeenCalled();

    track(store);
    track(store);
    await vi.waitFor(() => expect(store.getSnapshot().kind).toBe("signed_in"));
    expect(fetchMe).toHaveBeenCalledTimes(1);

    const state = store.getSnapshot();
    expect(state.kind === "signed_in" && state.me.display_name).toBe("Alice Anders");
  });

  it("returns a stable snapshot between changes", () => {
    const store = createSessionStore({ fetchMe: () => new Promise(() => {}) });
    expect(store.getSnapshot()).toBe(store.getSnapshot());
  });

  it("treats 401 as signed out", async () => {
    const store = createSessionStore({ fetchMe: async () => Promise.reject(unauthenticated()) });
    track(store);
    await vi.waitFor(() => expect(store.getSnapshot()).toEqual({ kind: "signed_out" }));
  });

  it("reports other failures as an error, not as signed out", async () => {
    const store = createSessionStore({
      fetchMe: async () => Promise.reject(new ApiError({ status: 503, title: "Service Unavailable" })),
    });
    track(store);
    await vi.waitFor(() =>
      expect(store.getSnapshot()).toEqual({ kind: "error", message: "Service Unavailable" }),
    );
  });

  it("reports a malformed /me payload as an error", async () => {
    const store = createSessionStore({ fetchMe: async () => ({ id: 1 }) });
    track(store);
    await vi.waitFor(() => expect(store.getSnapshot().kind).toBe("error"));
  });

  it("signs out: posts logout, goes signed out, then navigates to the redirect", async () => {
    const postLogout = vi.fn(async () => ({
      redirect_url: "http://localhost:8480/auth/realms/workmind/protocol/openid-connect/logout?x=1",
    }));
    const navigate = vi.fn();
    const store = createSessionStore({ fetchMe: async () => alice, postLogout, navigate });
    const { seen } = track(store);
    await vi.waitFor(() => expect(store.getSnapshot().kind).toBe("signed_in"));

    await store.signOut();
    expect(postLogout).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot()).toEqual({ kind: "signed_out" });
    expect(navigate).toHaveBeenCalledWith(
      "http://localhost:8480/auth/realms/workmind/protocol/openid-connect/logout?x=1",
    );
    expect(seen).toEqual(["signed_in", "signed_in", "signed_out"]); // loaded, signingOut, done
  });

  it("marks signing out in progress and ignores double clicks", async () => {
    const logout = deferred<unknown>();
    const postLogout = vi.fn(() => logout.promise);
    const store = createSessionStore({ fetchMe: async () => alice, postLogout, navigate: vi.fn() });
    track(store);
    await vi.waitFor(() => expect(store.getSnapshot().kind).toBe("signed_in"));

    const first = store.signOut();
    void store.signOut();
    expect(store.getSnapshot()).toMatchObject({ kind: "signed_in", signingOut: true });
    expect(postLogout).toHaveBeenCalledTimes(1);
    logout.resolve({ redirect_url: "/" });
    await first;
  });

  it("goes home if the redirect URL is missing or unsafe", async () => {
    const navigate = vi.fn();
    const store = createSessionStore({
      fetchMe: async () => alice,
      postLogout: async () => ({ redirect_url: "javascript:alert(1)" }),
      navigate,
    });
    track(store);
    await vi.waitFor(() => expect(store.getSnapshot().kind).toBe("signed_in"));
    await store.signOut();
    expect(navigate).toHaveBeenCalledWith("/");
  });

  it("treats a 401 on logout as already signed out, without navigating", async () => {
    const navigate = vi.fn();
    const store = createSessionStore({
      fetchMe: async () => alice,
      postLogout: async () => Promise.reject(unauthenticated()),
      navigate,
    });
    track(store);
    await vi.waitFor(() => expect(store.getSnapshot().kind).toBe("signed_in"));
    await store.signOut();
    expect(store.getSnapshot()).toEqual({ kind: "signed_out" });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("keeps the session and shows an error if logout fails", async () => {
    const store = createSessionStore({
      fetchMe: async () => alice,
      postLogout: async () =>
        Promise.reject(new ApiError({ status: 403, title: "Cross-site request blocked", code: "csrf_header" })),
      navigate: vi.fn(),
    });
    track(store);
    await vi.waitFor(() => expect(store.getSnapshot().kind).toBe("signed_in"));
    await store.signOut();
    expect(store.getSnapshot()).toMatchObject({
      kind: "signed_in",
      signingOut: false,
      signOutError: "Cross-site request blocked",
    });
  });

  it("does not let a slow load resurrect a session after sign-out", async () => {
    const slow = deferred<unknown>();
    let calls = 0;
    const store = createSessionStore({
      fetchMe: () => (++calls === 1 ? Promise.resolve(alice) : slow.promise),
      postLogout: async () => ({ redirect_url: "/" }),
      navigate: vi.fn(),
    });
    track(store);
    await vi.waitFor(() => expect(store.getSnapshot().kind).toBe("signed_in"));

    const reload = store.load(); // e.g. a refresh racing with sign-out
    await store.signOut();
    slow.resolve(alice);
    await reload;
    expect(store.getSnapshot()).toEqual({ kind: "signed_out" });
  });

  it("stops notifying after unsubscribe", async () => {
    const store = createSessionStore({ fetchMe: async () => alice });
    const { seen, unsubscribe } = track(store);
    unsubscribe();
    await store.load();
    expect(seen).toEqual([]);
  });
});

describe("safeRedirect", () => {
  it.each([
    ["http://localhost:8480/auth/logout", "http://localhost:8480/auth/logout"],
    ["https://sso.example.com/logout?a=1", "https://sso.example.com/logout?a=1"],
    ["/", "/"],
    ["//evil.example", "/"],
    ["javascript:alert(1)", "/"],
    ["data:text/html,hi", "/"],
    ["not a url", "/"],
    [undefined, "/"],
    [42, "/"],
  ])("%j → %s", (input, expected) => {
    expect(safeRedirect(input)).toBe(expected);
  });
});
