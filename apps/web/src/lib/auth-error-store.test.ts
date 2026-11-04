import { describe, expect, it, vi } from "vitest";

import { createAuthErrorStore } from "./auth-error-store";

function at(href: string) {
  const url = new URL(href);
  return () => ({ href: url.href, search: url.search });
}

describe("auth error store", () => {
  it("shows nothing and leaves the URL alone without an error", () => {
    const replaceUrl = vi.fn();
    const store = createAuthErrorStore({ getLocation: at("http://localhost:8480/knowledge"), replaceUrl });
    store.subscribe(() => {});
    expect(store.getSnapshot()).toBeNull();
    expect(replaceUrl).not.toHaveBeenCalled();
  });

  it("reads the code on subscribe, not during render, and cleans the URL once", () => {
    const replaceUrl = vi.fn();
    const store = createAuthErrorStore({
      getLocation: at("http://localhost:8480/?auth_error=no_organization&tab=1"),
      replaceUrl,
    });
    expect(store.getSnapshot()).toBeNull(); // first render (matches the server snapshot)
    expect(replaceUrl).not.toHaveBeenCalled();

    const listener = vi.fn();
    store.subscribe(listener);
    store.subscribe(() => {});
    expect(store.getSnapshot()).toMatchObject({ code: "no_organization", title: "No organization for this account" });
    expect(replaceUrl).toHaveBeenCalledTimes(1);
    expect(replaceUrl).toHaveBeenCalledWith("/?tab=1");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("dismisses and notifies", () => {
    const store = createAuthErrorStore({
      getLocation: at("http://localhost:8480/?auth_error=login_failed"),
      replaceUrl: () => {},
    });
    const listener = vi.fn();
    store.subscribe(listener);
    listener.mockClear();

    store.dismiss();
    expect(store.getSnapshot()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    store.dismiss();
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
