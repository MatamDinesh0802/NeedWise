import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, CSRF_HEADER } from "@/lib/api";
import { isEnterpriseFeatureError } from "@/lib/edition";

import { chargebackPath, eventsPath, summaryPath, usageApi, usageKeys, usageQuery } from "./api";

const fetchMock = vi.fn<typeof fetch>();

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" } });

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => json({}));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

function lastCall() {
  const [url, init] = fetchMock.mock.calls.at(-1)!;
  const headers = new Headers(init?.headers);
  return { url, method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined, headers };
}

const range = { from: "2026-11-01", to: "2026-11-12" };

describe("paths", () => {
  it("builds the summary query in a fixed order", () => {
    expect(summaryPath({ ...range, group_by: "model", interval: "day" })).toBe(
      "/api/v1/usage/summary?from=2026-11-01&to=2026-11-12&group_by=model&interval=day",
    );
  });

  it("skips empty event filters and encodes the cursor", () => {
    expect(eventsPath({ ...range, user_id: "", feature: "chat", model: null }, "a+b/c")).toBe(
      "/api/v1/usage/events?from=2026-11-01&to=2026-11-12&feature=chat&cursor=a%2Bb%2Fc",
    );
    expect(usageQuery({})).toBe("");
  });

  it("asks the chargeback report by department", () => {
    expect(chargebackPath(range)).toBe("/api/v1/usage/export.csv?from=2026-11-01&to=2026-11-12&group_by=department");
  });

  it("keys every input of the summary", () => {
    const a = usageKeys.summary({ ...range, group_by: "model", interval: "day" });
    const b = usageKeys.summary({ ...range, group_by: "user", interval: "day" });
    expect(a).not.toBe(b);
    expect(a.startsWith(usageKeys.all)).toBe(true);
    expect(usageKeys.events({ ...range, feature: "chat" })).toBe("usage/events?from=2026-11-01&to=2026-11-12&feature=chat");
  });
});

describe("usageApi", () => {
  it("reads the summary and events with GET and no CSRF header", async () => {
    await usageApi.summary({ ...range, group_by: "department", interval: "week" });
    expect(lastCall()).toMatchObject({ method: "GET", url: "/api/v1/usage/summary?from=2026-11-01&to=2026-11-12&group_by=department&interval=week" });
    expect(lastCall().headers.has(CSRF_HEADER)).toBe(false);
    await usageApi.events(range, "next");
    expect(lastCall().url).toBe("/api/v1/usage/events?from=2026-11-01&to=2026-11-12&cursor=next");
  });

  it("saves budgets with PUT, JSON and the CSRF header", async () => {
    const body = {
      budgets: [{ scope: "tenant" as const, subject_id: null, period: "month" as const, limit_usd: 500, warn_at: 0.8, action: "block" as const }],
      user_default: null,
    };
    await usageApi.saveBudgets(body);
    const call = lastCall();
    expect(call).toMatchObject({ method: "PUT", url: "/api/v1/admin/budgets", body });
    expect(call.headers.get(CSRF_HEADER)).toBe("1");
    expect(call.headers.get("Content-Type")).toBe("application/json");
    await usageApi.budgets();
    expect(lastCall()).toMatchObject({ method: "GET", url: "/api/v1/admin/budgets" });
  });

  it("downloads the chargeback CSV as a blob", async () => {
    fetchMock.mockImplementation(async () => new Response("department,cost_usd\nFinance,6.25\n", { status: 200, headers: { "Content-Type": "text/csv" } }));
    const blob = await usageApi.chargebackCsv(range);
    expect(await blob.text()).toContain("Finance,6.25");
    expect(lastCall().headers.get("Accept")).toBe("text/csv");
  });

  it("turns the 402 for an unlicensed chargeback report into an enterprise_feature error", async () => {
    fetchMock.mockImplementation(async () =>
      json({ type: "about:blank", title: "Enterprise feature", status: 402, code: "enterprise_feature", detail: "Chargeback reports is part of WorkMind Enterprise." }, 402),
    );
    const error = await usageApi.chargebackCsv(range).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(isEnterpriseFeatureError(error)).toBe(true);
  });

  it("keeps the 422 problem (with its error list) for the budgets form", async () => {
    fetchMock.mockImplementation(async () =>
      json({ title: "Invalid", status: 422, code: "validation_error", errors: [{ loc: ["body", "budgets", 0, "limit_usd"], msg: "must be > 0" }] }, 422),
    );
    const error = (await usageApi.saveBudgets({ budgets: [], user_default: null }).catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(422);
    expect(error.problem?.errors).toHaveLength(1);
  });
});
