import { describe, expect, it, vi } from "vitest";

import { createPagedList } from "./paged-list";
import { createQueryCache, type QueryState } from "./query-cache";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("query cache", () => {
  it("fetches on first subscription and shares the result", async () => {
    const cache = createQueryCache();
    const fetcher = vi.fn(async () => ({ items: [1] }));
    cache.subscribe("k", fetcher, () => {});
    cache.subscribe("k", fetcher, () => {});
    await vi.waitFor(() => expect(cache.getSnapshot("k").status).toBe("success"));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(cache.getSnapshot("k")).toEqual({ status: "success", data: { items: [1] }, refreshing: false });
  });

  it("keeps showing data while refreshing after invalidate", async () => {
    const cache = createQueryCache();
    let n = 0;
    cache.subscribe("admin/departments", async () => ++n, () => {});
    await vi.waitFor(() => expect(cache.getSnapshot("admin/departments").status).toBe("success"));

    const invalidated = cache.invalidate("admin/");
    expect(cache.getSnapshot("admin/departments")).toEqual({ status: "success", data: 1, refreshing: true });
    await invalidated;
    expect(cache.getSnapshot("admin/departments")).toEqual({ status: "success", data: 2, refreshing: false });
  });

  it("drops inactive entries on invalidate and refetches when they come back", async () => {
    const cache = createQueryCache();
    const fetcher = vi.fn(async () => "x");
    const unsubscribe = cache.subscribe("admin/groups", fetcher, () => {});
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    unsubscribe();
    await cache.invalidate("admin/groups");
    expect(cache.getSnapshot("admin/groups").status).toBe("loading");
    cache.subscribe("admin/groups", fetcher, () => {});
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  });

  it("records errors and lets the latest request win", async () => {
    const cache = createQueryCache();
    const slow = deferred<string>();
    const calls = [() => slow.promise, async () => "fresh"];
    let i = 0;
    cache.subscribe("k", () => calls[i++](), () => {});
    await cache.refetch("k"); // second request resolves first
    slow.resolve("stale");
    await slow.promise;
    expect((cache.getSnapshot("k") as QueryState<string> & { data?: string }).data).toBe("fresh");

    const failing = createQueryCache();
    failing.subscribe("e", async () => Promise.reject(new Error("down")), () => {});
    await vi.waitFor(() => expect(failing.getSnapshot("e").status).toBe("error"));
  });

  it("only touches keys under the prefix", async () => {
    const cache = createQueryCache();
    const a = vi.fn(async () => 1);
    const b = vi.fn(async () => 1);
    cache.subscribe("admin/users", a, () => {});
    cache.subscribe("me/tokens", b, () => {});
    await vi.waitFor(() => expect(b).toHaveBeenCalledTimes(1));
    await cache.invalidate("admin/");
    expect(a).toHaveBeenCalledTimes(2);
    expect(b).toHaveBeenCalledTimes(1);
  });
});

describe("paged list", () => {
  const pages: Record<string, { items: number[]; next: string | null }> = {
    start: { items: [1, 2], next: "p2" },
    p2: { items: [3], next: null },
  };

  it("loads the first page on subscribe and appends with loadMore", async () => {
    const fetchPage = vi.fn(async (cursor: string | null) => pages[cursor ?? "start"]);
    const list = createPagedList(fetchPage);
    expect(list.getSnapshot().status).toBe("loading");
    list.subscribe(() => {});
    await vi.waitFor(() => expect(list.getSnapshot().status).toBe("ready"));
    expect(list.getSnapshot()).toMatchObject({ items: [1, 2], hasMore: true });

    await list.loadMore();
    expect(list.getSnapshot()).toMatchObject({ items: [1, 2, 3], hasMore: false, loadingMore: false });
    expect(fetchPage.mock.calls.map((c) => c[0])).toEqual([null, "p2"]);

    await list.loadMore(); // no more pages
    expect(fetchPage).toHaveBeenCalledTimes(2);
  });

  it("keeps shown items when loading more fails", async () => {
    const list = createPagedList(async (cursor: string | null) => {
      if (cursor) throw new Error("timeout");
      return pages.start;
    });
    list.subscribe(() => {});
    await vi.waitFor(() => expect(list.getSnapshot().status).toBe("ready"));
    await list.loadMore();
    expect(list.getSnapshot()).toMatchObject({ items: [1, 2], hasMore: true, moreError: expect.any(Error) });
  });

  it("reports a failed first page as an error", async () => {
    const list = createPagedList(async () => Promise.reject(new Error("down")));
    list.subscribe(() => {});
    await vi.waitFor(() => expect(list.getSnapshot().status).toBe("error"));
  });

  it("updates and prepends items in place", async () => {
    const list = createPagedList(async () => ({ items: [{ id: 1, v: "a" }], next: null }));
    list.subscribe(() => {});
    await vi.waitFor(() => expect(list.getSnapshot().status).toBe("ready"));
    list.updateItem((x) => x.id === 1, { id: 1, v: "b" });
    list.prepend({ id: 2, v: "c" });
    expect(list.getSnapshot()).toMatchObject({ items: [{ id: 2, v: "c" }, { id: 1, v: "b" }] });
  });
});
