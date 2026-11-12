import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CSRF_HEADER } from "@/lib/api";

import {
  agentsApi,
  cleanExamples,
  DEFAULT_MODEL,
  deleteAgentText,
  diffSnapshots,
  emptyAgentForm,
  fetchModelCatalog,
  formatTestUsage,
  formFromAgent,
  modelLabel,
  modelOptions,
  restoreText,
  routingSummary,
  snapshotOf,
  sortTools,
  stepLabel,
  suggestAgentKey,
  testRequest,
  toAgentFields,
  toCreatePayload,
  toggledPayload,
  toUpdatePayload,
  validateAgentForm,
  withName,
  withTools,
  type Agent,
  type AgentForm,
  type AgentTool,
  type ModelCatalog,
} from "./agents";

const support: Agent = {
  id: "a1",
  key: "support",
  name: "Support agent",
  description: "Customer issues, SLAs, P1 response times and known workarounds.",
  instructions: "",
  collections: ["support-knowledge"],
  examples: ["What P1 response time do customers get?"],
  tools: [],
  model: null,
  max_searches: 2,
  max_tool_calls: 0,
  max_output_tokens: 6000,
  enabled: true,
  version: 3,
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

const catalog: ModelCatalog = {
  providers: [
    { id: "system", name: "Anthropic (deployment)", kind: "anthropic", base_url: null, local: false, has_key: true, key_hint: "…a1b2", enabled: true, system: true },
    { id: "p-local", name: "Ollama", kind: "openai_compatible", base_url: "http://ollama:11434/v1", local: true, has_key: false, key_hint: null, enabled: true, system: false },
    { id: "p-off", name: "Old vLLM", kind: "openai_compatible", base_url: "http://vllm/v1", local: true, has_key: false, key_hint: null, enabled: false, system: false },
  ],
  models: [
    { id: "system:claude-sonnet-5-5", provider_id: "system", name: "claude-sonnet-5-5", label: "Claude Sonnet 5.5", local: false, input_usd_per_mtok: 3, output_usd_per_mtok: 15, enabled: true },
    { id: "p-local:qwen3:8b", provider_id: "p-local", name: "qwen3:8b", label: "Qwen3 8B", local: true, input_usd_per_mtok: null, output_usd_per_mtok: null, enabled: true },
    { id: "system:claude-haiku", provider_id: "system", name: "claude-haiku", label: "Claude Haiku", local: false, input_usd_per_mtok: 1, output_usd_per_mtok: 5, enabled: false },
    { id: "p-off:llama", provider_id: "p-off", name: "llama", label: "Llama", local: true, input_usd_per_mtok: null, output_usd_per_mtok: null, enabled: true },
  ],
  roles: {
    answer: "system:claude-sonnet-5-5",
    router: "system:claude-sonnet-5-5",
    judge: "system:claude-sonnet-5-5",
    generator: "system:claude-sonnet-5-5",
    agent: "system:claude-sonnet-5-5",
  },
  policy: { max_external_sensitivity: "confidential", local_model: "p-local:qwen3:8b" },
  defaults: {
    roles: {
      answer: "system:claude-sonnet-5-5",
      router: "system:claude-sonnet-5-5",
      judge: "system:claude-sonnet-5-5",
      generator: "system:claude-sonnet-5-5",
      agent: "system:claude-sonnet-5-5",
    },
    policy: { max_external_sensitivity: "confidential", local_model: null },
  },
};

describe("key suggestion", () => {
  it("suggests a slug from the name, at most 40 characters", () => {
    expect(suggestAgentKey("Legal & Compliance")).toBe("legal-compliance");
    // docs/agents.md: "Legal agent" gets the key the routing evaluation expects.
    expect(suggestAgentKey("Legal agent")).toBe("legal");
    expect(suggestAgentKey("Customer Support Agent")).toBe("customer-support");
    expect(suggestAgentKey("Agent")).toBe("agent");
    expect(suggestAgentKey("Agent Smith")).toBe("agent-smith");
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
      { ...emptyAgentForm(), key: "Bad Key", description: "short", maxSearches: 9, maxToolCalls: 7, maxOutputTokens: 50, note: "x".repeat(501) },
      { creating: true },
    );
    expect(Object.keys(errors).sort()).toEqual([
      "collections",
      "description",
      "key",
      "maxOutputTokens",
      "maxSearches",
      "maxToolCalls",
      "name",
      "note",
    ]);
    expect(errors.description).toMatch(/at least 10 characters/);
    expect(errors.collections).toMatch(/at least one collection/);
  });

  it("needs a tool call when tools are chosen", () => {
    expect(validateAgentForm({ ...valid(), tools: ["draft_email"], maxToolCalls: 0 }, { creating: true }).maxToolCalls).toMatch(
      /at least one tool call/,
    );
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

describe("tools", () => {
  it("gives the first tool a call budget and takes it away with the last", () => {
    const one = withTools(valid(), ["draft_email", "draft_email"]);
    expect(one.tools).toEqual(["draft_email"]);
    expect(one.maxToolCalls).toBe(2);
    expect(withTools({ ...one, maxToolCalls: 4 }, ["draft_email", "query_data"]).maxToolCalls).toBe(4);
    expect(withTools(one, []).maxToolCalls).toBe(0);
  });

  it("lists the tools an agent may have first", () => {
    const tool = (name: string, forAgents: boolean): AgentTool => ({
      name,
      description: "",
      side_effect: forAgents ? "read" : "external",
      scope: "assistant:ask",
      source: "builtin",
      for_agents: forAgents,
      not_for_agents_reason: forAgents ? null : "needs approval",
    });
    expect(sortTools([tool("send_email", false), tool("query_data", true), tool("draft_email", true)]).map((t) => t.name)).toEqual([
      "draft_email",
      "query_data",
      "send_email",
    ]);
  });
});

describe("model choice", () => {
  it("offers the role default and the enabled models of enabled providers", () => {
    expect(modelOptions(catalog, null)).toEqual([
      { value: DEFAULT_MODEL, label: "Default for its role (Claude Sonnet 5.5)" },
      { value: "system:claude-sonnet-5-5", label: "Claude Sonnet 5.5 · Anthropic (deployment)" },
      { value: "p-local:qwen3:8b", label: "Qwen3 8B · local" },
    ]);
  });

  it("offers only the default without a catalog, keeping a saved model selectable", () => {
    expect(modelOptions(null, null)).toEqual([{ value: DEFAULT_MODEL, label: "Default for its role" }]);
    expect(modelOptions(null, "system:claude-x").map((o) => o.label)).toEqual([
      "Default for its role",
      "system:claude-x (not in the model catalog)",
    ]);
  });

  it("names the model on the card", () => {
    expect(modelLabel(catalog, null)).toBe("Default model");
    expect(modelLabel(catalog, "p-local:qwen3:8b")).toBe("Qwen3 8B");
    expect(modelLabel(null, "system:claude-x")).toBe("system:claude-x");
  });
});

describe("payload mapping", () => {
  it("trims, drops blank and duplicate examples, and maps every field", () => {
    expect(
      toCreatePayload({
        ...withTools(valid(), ["draft_email"]),
        maxSearches: 3,
        enabled: false,
        name: "  Legal agent ",
        model: "system:claude-sonnet-5-5",
        maxOutputTokens: 4000,
        note: "  First version ",
      }),
    ).toEqual({
      key: "legal-agent",
      name: "Legal agent",
      description: "Contracts, NDAs and data processing agreements.",
      instructions: "",
      collections: ["legal-contracts"],
      examples: ["Do we have an NDA with Kestrel?", "Who signs DPAs?"],
      tools: ["draft_email"],
      model: "system:claude-sonnet-5-5",
      max_searches: 3,
      max_tool_calls: 2,
      max_output_tokens: 4000,
      enabled: false,
      note: "First version",
    });
  });

  it("sends the default model as null and no empty note", () => {
    const body = toUpdatePayload(formFromAgent(support));
    expect(body.model).toBeNull();
    expect(body).not.toHaveProperty("note");
    expect(body).not.toHaveProperty("key");
  });

  it("sends no key on update and dedupes collections", () => {
    const fields = toAgentFields({ ...formFromAgent(support), collections: ["support-knowledge", "support-knowledge"] });
    expect(fields).not.toHaveProperty("key");
    expect(fields.collections).toEqual(["support-knowledge"]);
  });

  it("round-trips an existing agent unchanged", () => {
    const { id: _id, key: _key, updated_at: _updated, version: _version, ...stored } = support;
    void _id;
    void _key;
    void _updated;
    void _version;
    expect(toAgentFields(formFromAgent(support))).toEqual(stored);
  });

  it("toggling keeps every other field and notes it", () => {
    expect(toggledPayload(support, false)).toEqual({ ...toAgentFields(formFromAgent(support)), enabled: false, note: "Disabled" });
  });

  it("cleans examples case-insensitively", () => {
    expect(cleanExamples(["A?", "a?", " ", "B"])).toEqual(["A?", "B"]);
  });
});

describe("test console request", () => {
  it("sends a new agent's draft with its key", () => {
    const request = testRequest(valid(), "  Who signs DPAs? ", null);
    expect(request.question).toBe("Who signs DPAs?");
    expect(request.key).toBeUndefined();
    expect(request.draft?.key).toBe("legal-agent");
    expect(request.draft?.examples).toEqual(["Do we have an NDA with Kestrel?", "Who signs DPAs?"]);
  });

  it("puts an edited draft in place of the saved agent", () => {
    const request = testRequest({ ...formFromAgent(support), description: "Edited, unsaved text." }, "q", "support");
    expect(request.key).toBe("support");
    expect(request.draft?.description).toBe("Edited, unsaved text.");
    expect(request.draft).not.toHaveProperty("key");
  });
});

describe("console results", () => {
  it("summarizes routing", () => {
    const base = { routed_by: "planner" as const, reason: null, tested_agent_picked: true };
    expect(routingSummary({ ...base, mode: "direct", agents: [] })).toBe("No agent: the assistant answers directly");
    expect(routingSummary({ ...base, mode: "single", agents: [{ key: "legal", name: "Legal agent", goal: "g" }] })).toBe("Legal agent");
    expect(
      routingSummary({
        ...base,
        mode: "fan_out",
        agents: [
          { key: "legal", name: "Legal agent", goal: "g" },
          { key: "finance", name: "Finance agent", goal: "g" },
          { key: "sales", name: "Sales agent", goal: "g" },
        ],
      }),
    ).toBe("Legal agent, Finance agent and Sales agent");
  });

  it("formats usage and cost, saying when a price is unknown", () => {
    expect(formatTestUsage({ calls: 4, input_tokens: 3120, output_tokens: 410, cost_usd: 0.0042, models: [] })).toBe(
      "4 calls · 3,120 in · 410 out · $0.004",
    );
    expect(formatTestUsage({ calls: 1, input_tokens: 10, output_tokens: 2, cost_usd: null, models: [] })).toBe(
      "1 call · 10 in · 2 out · cost not reported",
    );
  });

  it("explains each step", () => {
    expect(stepLabel({ kind: "search", detail: "NDA rules", ok: true, error: null })).toBe("Searched “NDA rules”");
    expect(stepLabel({ kind: "tool", detail: "draft_email", ok: true, error: null })).toBe("Called draft_email");
    expect(stepLabel({ kind: "tool", detail: "draft_email", ok: false, error: "limit_reached" })).toBe(
      "Tried draft_email: over the tool-call limit, not run",
    );
  });
});

describe("versions", () => {
  it("diffs a version against the current definition, field by field", () => {
    const current = snapshotOf({ ...support, examples: [...support.examples, "Known issues?"], tools: ["draft_email"], max_tool_calls: 2 });
    const changes = diffSnapshots(snapshotOf(support), current);
    expect(changes.map((c) => c.label)).toEqual(["Example questions", "Tools", "Tool calls per question"]);
    expect(changes[1]).toEqual({ field: "tools", label: "Tools", before: "(none)", after: "draft_email" });
    expect(diffSnapshots(snapshotOf(support), snapshotOf(support))).toEqual([]);
  });

  it("marks fields an older version did not record", () => {
    const { tools: _tools, ...old } = snapshotOf(support);
    void _tools;
    expect(diffSnapshots(old, snapshotOf(support))).toEqual([
      { field: "tools", label: "Tools", before: "(not recorded)", after: "(none)" },
    ]);
  });

  it("says restoring adds a version and removes nothing", () => {
    const text = restoreText(support, 1);
    expect(text.title).toBe("Restore version 1 of Support agent?");
    expect(text.description).toContain("saves version 4, equal to version 1");
    expect(text.description).toContain("Nothing in the history is removed");
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

  it("deletes by key with DELETE and CSRF (204, no body)", async () => {
    fetchMock.mockImplementationOnce(async () => new Response(null, { status: 204 }));
    await expect(agentsApi.remove("customer support")).resolves.toBeUndefined();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/admin/agents/customer%20support");
    expect(init?.method).toBe("DELETE");
    expect(new Headers(init?.headers).get(CSRF_HEADER)).toBe("1");
  });

  it("lists versions, restores one and runs the console", async () => {
    await agentsApi.versions("legal");
    await agentsApi.restore("legal", 2);
    await agentsApi.test({ question: "q", key: "legal" });
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/admin/agents/legal/versions", "GET"],
      ["/api/v1/admin/agents/legal/versions/2/restore", "POST"],
      ["/api/v1/admin/agents/test", "POST"],
    ]);
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get(CSRF_HEADER)).toBe("1");
  });

  it("treats a missing model catalog (404) as none, and other errors as errors", async () => {
    fetchMock.mockImplementationOnce(
      async () => new Response(JSON.stringify({ title: "Not Found", status: 404 }), { status: 404, headers: { "Content-Type": "application/json" } }),
    );
    await expect(fetchModelCatalog()).resolves.toBeNull();
    fetchMock.mockImplementationOnce(async () => new Response("", { status: 500 }));
    await expect(fetchModelCatalog()).rejects.toMatchObject({ status: 500 });
  });
});

describe("deleting an agent", () => {
  it("asks by name and key, and says what stays and what goes", () => {
    const text = deleteAgentText(support);
    expect(text.title).toBe("Delete Support agent?");
    expect(text.description).toContain("“Support agent” (support)");
    expect(text.description).toContain("not touched");
    expect(text.description).toContain("version history is deleted");
    expect(text.description).toContain("can't be undone");
    expect(text.confirm).toBe("Delete agent");
  });
});
