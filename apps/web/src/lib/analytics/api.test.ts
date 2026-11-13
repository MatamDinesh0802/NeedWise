import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, CSRF_HEADER } from "@/lib/api";

import { analyticsApi, analyticsKeys, tableContentType, XLSX_TYPE } from "./api";

const fetchMock = vi.fn<typeof fetch>();

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => json({}));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

function lastCall() {
  const [url, init] = fetchMock.mock.calls.at(-1)!;
  return { url, method: init?.method, body: init?.body, headers: new Headers(init?.headers), signal: init?.signal };
}

describe("analyticsApi", () => {
  it("reads the model and saved runs with GET and no CSRF header", async () => {
    await analyticsApi.model();
    expect(lastCall()).toMatchObject({ url: "/api/v1/analytics/model", method: "GET" });
    expect(lastCall().headers.has(CSRF_HEADER)).toBe(false);
    await analyticsApi.runs();
    expect(lastCall().url).toBe("/api/v1/analytics/runs");
    await analyticsApi.run("r/1");
    expect(lastCall().url).toBe("/api/v1/analytics/runs/r%2F1");
  });

  it("asks with a JSON body, the CSRF header and an abort signal", async () => {
    const controller = new AbortController();
    await analyticsApi.ask("Monthly revenue trend this year", controller.signal);
    const call = lastCall();
    expect(call).toMatchObject({ url: "/api/v1/analytics/ask", method: "POST" });
    expect(JSON.parse(String(call.body))).toEqual({ question: "Monthly revenue trend this year" });
    expect(call.headers.get(CSRF_HEADER)).toBe("1");
    expect(call.headers.get("Content-Type")).toBe("application/json");
    expect(call.signal).toBe(controller.signal);
  });

  it("returns answers with status failed or no_access instead of throwing", async () => {
    fetchMock.mockResolvedValueOnce(json({ id: "a1", status: "no_access", error: "You may not query this data." }));
    await expect(analyticsApi.ask("x")).resolves.toMatchObject({ status: "no_access" });
  });

  it("re-runs SQL and surfaces sql_rejected as an ApiError with the reason", async () => {
    await analyticsApi.query("SELECT 1");
    expect(lastCall()).toMatchObject({ url: "/api/v1/analytics/query", method: "POST" });
    expect(JSON.parse(String(lastCall().body))).toEqual({ sql: "SELECT 1" });

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ title: "SQL rejected", status: 422, code: "sql_rejected", detail: "Only SELECT statements are allowed." }), {
        status: 422,
        headers: { "Content-Type": "application/problem+json" },
      }),
    );
    const error = await analyticsApi.query("DELETE FROM customers").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "sql_rejected", detail: "Only SELECT statements are allowed." });
  });

  it("calls the admin routes with the right methods", async () => {
    await analyticsApi.admin();
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/analytics", method: "GET" });

    await analyticsApi.saveModel("name: x\n");
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/analytics/model", method: "PUT" });
    expect(JSON.parse(String(lastCall().body))).toEqual({ yaml: "name: x\n" });
    expect(lastCall().headers.get(CSRF_HEADER)).toBe("1");

    await analyticsApi.deleteTable("invoice lines");
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/analytics/tables/invoice%20lines", method: "DELETE" });
    expect(lastCall().headers.get(CSRF_HEADER)).toBe("1");
  });

  it("starts a streamed answer and names its event stream", async () => {
    fetchMock.mockResolvedValueOnce(json({ run_id: "r 1" }, 202));
    await expect(analyticsApi.startRun("Shop revenue?")).resolves.toEqual({ run_id: "r 1" });
    expect(lastCall()).toMatchObject({ url: "/api/v1/analytics/runs", method: "POST" });
    expect(JSON.parse(String(lastCall().body))).toEqual({ question: "Shop revenue?" });
    expect(lastCall().headers.get(CSRF_HEADER)).toBe("1");
    expect(analyticsApi.eventsUrl("r 1")).toBe("/api/v1/analytics/runs/r%201/events");
  });

  it("re-runs a query on its dataset, and sends no dataset for the uploaded tables", async () => {
    await analyticsApi.query("SELECT 1", "shop");
    expect(JSON.parse(String(lastCall().body))).toEqual({ sql: "SELECT 1", dataset: "shop" });
    await analyticsApi.query("SELECT 1", "uploads");
    expect(JSON.parse(String(lastCall().body))).toEqual({ sql: "SELECT 1" });
  });

  it("calls the database source routes, sending the password only to create or replace it", async () => {
    await analyticsApi.sources();
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/analytics/sources", method: "GET" });
    const input = {
      key: "shop",
      name: "Shop",
      kind: "postgres" as const,
      host: "db",
      database: "shop",
      username: "reader",
      password: "s3cret",
      tls_mode: "verify-full" as const,
      schemas: ["shop"],
    };
    await analyticsApi.createSource(input);
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/analytics/sources", method: "POST" });
    expect(JSON.parse(String(lastCall().body))).toEqual(input);
    await analyticsApi.updateSource("shop", { enabled: false });
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/analytics/sources/shop", method: "PATCH" });
    expect(JSON.parse(String(lastCall().body))).toEqual({ enabled: false });
    await analyticsApi.testSource("shop");
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/analytics/sources/shop/test", method: "POST" });
    await analyticsApi.sourceCatalog("shop");
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/analytics/sources/shop/catalog", method: "GET" });
    await analyticsApi.saveSourceModel("shop", "name: x\n");
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/analytics/sources/shop/model", method: "PUT" });
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(analyticsApi.deleteSource("shop")).resolves.toBeUndefined();
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/analytics/sources/shop", method: "DELETE" });
  });

  it("uploads a table as the raw request body with its content type", async () => {
    const file = new Blob(["customer_id,name\nC001,Bluefin\n"], { type: "text/csv" });
    await analyticsApi.uploadTable("customers", file, "text/csv");
    const call = lastCall();
    expect(call).toMatchObject({ url: "/api/v1/admin/analytics/tables?name=customers", method: "POST" });
    expect(call.body).toBe(file);
    expect(call.headers.get("Content-Type")).toBe("text/csv");
    expect(call.headers.get(CSRF_HEADER)).toBe("1");
  });
});

describe("tableContentType", () => {
  it.each([
    ["customers.csv", "text/csv"],
    ["Revenue.XLSX", XLSX_TYPE],
    ["notes.txt", null],
    ["old.xls", null],
  ])("%s → %s", (name, type) => {
    expect(tableContentType(name)).toBe(type);
  });
});

describe("analyticsKeys", () => {
  it("keeps every key under one prefix for invalidation", () => {
    for (const key of [analyticsKeys.model, analyticsKeys.runs, analyticsKeys.run("r1"), analyticsKeys.admin, analyticsKeys.sources]) {
      expect(key.startsWith(analyticsKeys.all)).toBe(true);
    }
  });
});
