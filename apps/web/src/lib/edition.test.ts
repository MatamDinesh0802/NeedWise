import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, CSRF_HEADER } from "@/lib/api";

import {
  ENTERPRISE_FEATURES,
  editionApi,
  enterpriseNotice,
  featureLabel,
  hasFeature,
  isEnterpriseFeatureError,
  type EditionInfo,
} from "./edition";

const fetchMock = vi.fn<typeof fetch>();

const community: EditionInfo = { edition: "community", features: [], licensee: null, expires_at: null };
const enterprise: EditionInfo = {
  edition: "enterprise",
  features: ["retention_policies", "chargeback_reports"],
  licensee: "Acme Robotics",
  expires_at: "2026-12-12T00:00:00Z",
};

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(
    async () => new Response(JSON.stringify(community), { headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

describe("editionApi", () => {
  it("reads GET /api/v1/edition without a CSRF header", async () => {
    await expect(editionApi.get()).resolves.toEqual(community);
    const [url, init] = fetchMock.mock.calls.at(-1)!;
    expect(url).toBe("/api/v1/edition");
    expect(init?.method).toBe("GET");
    expect(new Headers(init?.headers).has(CSRF_HEADER)).toBe(false);
  });
});

describe("hasFeature", () => {
  it("is true only for licensed features of an Enterprise edition", () => {
    expect(hasFeature(enterprise, "retention_policies")).toBe(true);
    expect(hasFeature(enterprise, "chargeback_reports")).toBe(true);
    expect(hasFeature(enterprise, "department_budgets")).toBe(false);
    expect(hasFeature(community, "retention_policies")).toBe(false);
  });

  it("is false while the edition is unknown, and never trusts features in Community", () => {
    expect(hasFeature(null, "retention_policies")).toBe(false);
    expect(hasFeature(undefined, "retention_policies")).toBe(false);
    expect(hasFeature({ ...community, features: ["retention_policies"] }, "retention_policies")).toBe(false);
  });
});

describe("feature names", () => {
  it("match the API's contract", () => {
    expect(Object.keys(ENTERPRISE_FEATURES)).toEqual([
      "retention_policies",
      "department_budgets",
      "chargeback_reports",
      "advanced_audit_export",
    ]);
    expect(featureLabel("advanced_audit_export")).toBe("Advanced audit export");
  });
});

describe("isEnterpriseFeatureError", () => {
  it("recognises the API's 402 enterprise_feature problem only", () => {
    expect(isEnterpriseFeatureError(new ApiError({ status: 402, title: "Enterprise feature", code: "enterprise_feature" }))).toBe(true);
    expect(isEnterpriseFeatureError(new ApiError({ status: 403, title: "Forbidden", code: "enterprise_feature" }))).toBe(false);
    expect(isEnterpriseFeatureError(new ApiError({ status: 402, title: "Payment", code: "other" }))).toBe(false);
    expect(isEnterpriseFeatureError(new Error("enterprise_feature"))).toBe(false);
  });
});

describe("enterpriseNotice", () => {
  it("names the feature and says a license is missing", () => {
    const notice = enterpriseNotice("department_budgets");
    expect(notice.title).toBe("Enterprise feature: Department budgets");
    expect(notice.description).toContain("Spending limits");
    expect(notice.description).toContain("Enterprise license");
  });
});
