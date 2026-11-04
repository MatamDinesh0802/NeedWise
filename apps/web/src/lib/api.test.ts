import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, ApiError, apiJson, buildRequestInit, CSRF_HEADER, parseProblem } from "./api";

function problemResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/problem+json", ...headers },
  });
}

describe("buildRequestInit", () => {
  it.each(["GET", "HEAD", "OPTIONS"])("does not add the CSRF header for %s", (method) => {
    const headers = new Headers(buildRequestInit({ method }).headers);
    expect(headers.has(CSRF_HEADER)).toBe(false);
  });

  it.each(["POST", "PUT", "PATCH", "DELETE", "post", "delete"])("adds the CSRF header for %s", (method) => {
    const init = buildRequestInit({ method });
    expect(new Headers(init.headers).get(CSRF_HEADER)).toBe("1");
    expect(init.method).toBe(method.toUpperCase());
  });

  it("keeps caller headers, defaults Accept, and stays same-origin and uncached", () => {
    const init = buildRequestInit({ method: "POST", headers: { "X-Trace": "abc" } });
    const headers = new Headers(init.headers);
    expect(headers.get("X-Trace")).toBe("abc");
    expect(headers.get("Accept")).toBe("application/json");
    expect(init.credentials).toBe("same-origin");
    expect(init.cache).toBe("no-store");
  });

  it("serializes `json` with a JSON content type", () => {
    const init = buildRequestInit({ method: "PATCH", json: { name: "Acme" } });
    expect(init.body).toBe('{"name":"Acme"}');
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  });

  it("rejects passing both `json` and `body`", () => {
    expect(() => buildRequestInit({ method: "POST", json: {}, body: "x" })).toThrow();
  });
});

describe("parseProblem", () => {
  it("parses an RFC 9457 document with the API's code", () => {
    expect(
      parseProblem(
        { type: "about:blank", title: "Not signed in", status: 401, code: "unauthenticated", detail: "Sign in." },
        500,
      ),
    ).toMatchObject({ title: "Not signed in", status: 401, code: "unauthenticated", detail: "Sign in." });
  });

  it("fills defaults and keeps extension members", () => {
    expect(parseProblem({ title: "Nope", errors: ["x"] }, 422)).toEqual({
      title: "Nope",
      type: "about:blank",
      status: 422,
      code: undefined,
      detail: undefined,
      errors: ["x"],
    });
  });

  it.each([null, "text", [], { detail: "no title" }])("returns null for %j", (body) => {
    expect(parseProblem(body, 500)).toBeNull();
  });
});

describe("apiFetch", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the CSRF header on POST and returns 2xx responses", async () => {
    fetchMock.mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
    await expect(apiJson("/api/auth/logout", { method: "POST" })).resolves.toEqual({ ok: true });

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/auth/logout");
    expect(new Headers(init?.headers).get(CSRF_HEADER)).toBe("1");
  });

  it("throws ApiError with the problem details for error responses", async () => {
    fetchMock.mockResolvedValue(
      problemResponse(
        403,
        { type: "about:blank", title: "Cross-site request blocked", status: 403, code: "csrf_origin" },
        { "X-Request-Id": "req-123" },
      ),
    );
    const error = await apiFetch("/api/v1/things", { method: "DELETE" }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 403,
      code: "csrf_origin",
      title: "Cross-site request blocked",
      requestId: "req-123",
    });
  });

  it("flags 401 as unauthenticated", async () => {
    fetchMock.mockResolvedValue(
      problemResponse(401, { title: "Not signed in", status: 401, code: "unauthenticated", detail: "Sign in to continue." }),
    );
    const error = (await apiJson("/api/v1/me").catch((e: unknown) => e)) as ApiError;
    expect(error.isUnauthenticated).toBe(true);
    expect(error.message).toBe("Not signed in: Sign in to continue.");
  });

  it("falls back to the status line for non-JSON errors", async () => {
    fetchMock.mockResolvedValue(new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" }));
    const error = (await apiFetch("/api/v1/me").catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({ status: 502, title: "Bad Gateway", code: null, problem: null });
  });

  it("survives malformed JSON error bodies", async () => {
    fetchMock.mockResolvedValue(
      new Response("{not json", { status: 500, headers: { "Content-Type": "application/json" } }),
    );
    const error = (await apiFetch("/api/v1/me").catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({ status: 500, problem: null });
  });

  it("wraps network failures as ApiError status 0", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = (await apiFetch("/api/v1/me").catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({ status: 0, code: "network_error", detail: "Failed to fetch" });
  });

  it("returns undefined for 204 No Content", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(apiJson("/api/v1/thing", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("refuses non-API and absolute URLs", async () => {
    await expect(apiFetch("https://evil.example/api/v1/me")).rejects.toThrow(/same-origin/);
    await expect(apiFetch("/healthz")).rejects.toThrow(/same-origin/);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
