import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CSRF_HEADER } from "@/lib/api";

import {
  agentsApi,
  cleanExamples,
  emptyAgentForm,
  formFromAgent,
  suggestAgentKey,
  toAgentFields,
  toCreatePayload,
  toggledPayload,
  validateAgentForm,
  withName,
  type Agent,
  type AgentForm,
} from "./agents";

const support: Agent = {
  id: "a1",
  key: "support",
  name: "Support agent",
  description: "Customer issues, SLAs, P1 response times and known workarounds.",
  instructions: "",
  collections: ["support-knowledge"],
  examples: ["What P1 response time do customers get?"],
  max_searches: 2,
  enabled: true,
  updated_at: "2026-11-04T10:00:00Z",
};

const valid = (): AgentForm => ({
  ...emptyAgentForm(),
  name: "Legal agent",
  key: "legal-agent",
  description: "Contracts, NDAs and data processing agreements.",
  collections: ["legal-contracts"],
  examples: ["  Do we have an NDA with Kestrel?  ", "", "do we have an nda with kestrel?", "Who signs DPAs?"],
});

describe("key suggestion", () => {
  it("suggests a slug from the name, at most 40 characters", () => {
    expect(suggestAgentKey("Legal & Compliance")).toBe("legal-compliance");
    const long = suggestAgentKey("A very long agent name for the procurement and vendor management team");
    expect(long.length).toBeLessThanOrEqual(40);
    expect(long.endsWith("-")).toBe(false);
  });

  it("follows the name until the key is edited by hand", () => {
    let form = withName(emptyAgentForm(), "Legal");
    expect(form.key).toBe("legal");
    form = withName({ ...form, key: "contracts", keyEdited: true }, "Legal team");
    expect(form.key).toBe("contracts");
  });
});

describe("validation", () => {
  it("accepts a valid form", () => {
    expect(validateAgentForm(valid(), { creating: true })).toEqual({});
  });

  it("explains each problem", () => {
    const errors = validateAgentForm(
      { ...emptyAgentForm(), key: "Bad Key", description: "short", maxSearches: 9 },
      { creating: true },
    );
    expect(Object.keys(errors).sort()).toEqual(["collections", "description", "key", "maxSearches", "name"]);
    expect(errors.description).toMatch(/at least 10 characters/);
    expect(errors.collections).toMatch(/at least one collection/);
  });

  it("checks the key only when creating (it can't change)", () => {
    expect(validateAgentForm({ ...valid(), key: "" }, { creating: false }).key).toBeUndefined();
    expect(validateAgentForm({ ...valid(), key: "x".repeat(41) }, { creating: true }).key).toMatch(/40/);
  });

  it("limits examples to 10 after cleaning", () => {
    const many = Array.from({ length: 11 }, (_, i) => `Question ${i}?`);
    expect(validateAgentForm({ ...valid(), examples: many }, { creating: true }).examples).toMatch(/10/);
    expect(validateAgentForm({ ...valid(), examples: [...many.slice(0, 10), "", "  "] }, { creating: true }).examples).toBeUndefined();
  });
});

describe("payload mapping", () => {
  it("trims, drops blank and duplicate examples, and maps maxSearches", () => {
    expect(toCreatePayload({ ...valid(), maxSearches: 3, enabled: false, name: "  Legal agent " })).toEqual({
      key: "legal-agent",
      name: "Legal agent",
      description: "Contracts, NDAs and data processing agreements.",
      instructions: "",
      collections: ["legal-contracts"],
      examples: ["Do we have an NDA with Kestrel?", "Who signs DPAs?"],
      max_searches: 3,
      enabled: false,
    });
  });

  it("sends no key on update and dedupes collections", () => {
    const fields = toAgentFields({ ...formFromAgent(support), collections: ["support-knowledge", "support-knowledge"] });
    expect(fields).not.toHaveProperty("key");
    expect(fields.collections).toEqual(["support-knowledge"]);
  });

  it("round-trips an existing agent unchanged", () => {
    const { id: _id, key: _key, updated_at: _updated, ...stored } = support;
    void _id;
    void _key;
    void _updated;
    expect(toAgentFields(formFromAgent(support))).toEqual(stored);
  });

  it("toggling keeps every other field", () => {
    expect(toggledPayload(support, false)).toEqual({ ...toAgentFields(formFromAgent(support)), enabled: false });
  });

  it("cleans examples case-insensitively", () => {
    expect(cleanExamples(["A?", "a?", " ", "B"])).toEqual(["A?", "B"]);
  });
});

describe("agentsApi", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("creates with POST and updates by key with PUT, with CSRF", async () => {
    await agentsApi.create(toCreatePayload(valid()));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/admin/agents");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    await agentsApi.update("support", toggledPayload(support, false));
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/v1/admin/agents/support");
    expect(init?.method).toBe("PUT");
    expect(new Headers(init?.headers).get(CSRF_HEADER)).toBe("1");
    expect(JSON.parse(String(init?.body)).enabled).toBe(false);
  });
});
