import { describe, expect, it } from "vitest";

import {
  catalogModelPath,
  emptyModelForm,
  emptyProviderForm,
  formatPrice,
  formFromModel,
  formFromProvider,
  levelsAbove,
  localModelOptions,
  parsePrice,
  policyExplanation,
  probeSummary,
  roleOptions,
  rolesPayload,
  toModelCreate,
  toModelPatch,
  toProviderCreate,
  toProviderPatch,
  validateModelForm,
  validateProviderForm,
  type CatalogModel,
  type ModelCatalog,
  type ModelProvider,
  type RoleModels,
} from "./models";

const SONNET = "system:claude-sonnet-5-5";
const system: ModelProvider = {
  id: "system",
  name: "Deployment default",
  kind: "anthropic",
  base_url: null,
  local: false,
  has_key: true,
  key_hint: null,
  enabled: true,
  system: true,
};
const team: ModelProvider = {
  id: "p-1",
  name: "Team OpenAI",
  kind: "openai_compatible",
  base_url: "https://api.openai.com/v1",
  local: false,
  has_key: true,
  key_hint: "…a1b2",
  enabled: true,
  system: false,
};
const onPrem: ModelProvider = { ...team, id: "p-2", name: "On-prem", base_url: "http://gpu:11434/v1", local: true, has_key: false, key_hint: null };
const model = (overrides: Partial<CatalogModel>): CatalogModel => ({
  id: SONNET,
  provider_id: "system",
  name: "claude-sonnet-5-5",
  label: "claude-sonnet-5-5",
  local: false,
  input_usd_per_mtok: 2,
  output_usd_per_mtok: 10,
  enabled: true,
  ...overrides,
});
const roles = (id: string): RoleModels => ({ answer: id, router: id, judge: id, generator: id, agent: id });
const catalog: ModelCatalog = {
  providers: [system, team, onPrem],
  models: [
    model({}),
    model({ id: "p-1:gpt-team", provider_id: "p-1", name: "gpt-team", label: "GPT team", input_usd_per_mtok: null, output_usd_per_mtok: null }),
    model({ id: "p-1:old", provider_id: "p-1", name: "old", label: "Old", enabled: false }),
    model({ id: "p-2:qwen3:14b", provider_id: "p-2", name: "qwen3:14b", label: "Qwen 3", local: true }),
  ],
  roles: roles(SONNET),
  policy: { max_external_sensitivity: "confidential", local_model: null },
  defaults: { roles: roles(SONNET), policy: { max_external_sensitivity: "confidential", local_model: null } },
};

describe("catalog ids in URLs", () => {
  it("keeps the slashes of model names and encodes the rest", () => {
    expect(catalogModelPath("p-2:meta-llama/Llama 4")).toBe("/api/v1/admin/models/catalog/p-2%3Ameta-llama/Llama%204");
    expect(catalogModelPath(SONNET)).toBe("/api/v1/admin/models/catalog/system%3Aclaude-sonnet-5-5");
  });
});

describe("prices", () => {
  it("formats both prices, or says the cost is unknown", () => {
    expect(formatPrice(model({}))).toBe("$2.00 in · $10.00 out per million tokens");
    expect(formatPrice(model({ input_usd_per_mtok: 0.15, output_usd_per_mtok: null }))).toBe("No price: costs show as unknown");
  });

  it("parses dollars, empty as no price, anything else as invalid", () => {
    expect(parsePrice(" $2.50 ")).toBe(2.5);
    expect(parsePrice("")).toBeNull();
    expect(parsePrice("two")).toBeNaN();
    expect(parsePrice("-1")).toBeNaN();
  });
});

describe("provider form", () => {
  it("needs a name, and a URL for OpenAI-compatible servers", () => {
    const errors = validateProviderForm(emptyProviderForm());
    expect(errors.name).toBeDefined();
    expect(errors.baseUrl).toMatch(/Enter the server's URL/);
    expect(validateProviderForm({ ...emptyProviderForm(), name: "Claude", kind: "anthropic" })).toEqual({});
  });

  it.each([
    ["ftp://llm.example", /Only http/],
    ["https://user:pw@llm.example/v1", /API key field/],
    ["https://llm.example/v1?key=1", /query/],
    ["http://169.254.169.254/latest", /metadata/],
    ["not a url", /full URL/],
  ])("refuses %s", (url, message) => {
    expect(validateProviderForm({ ...emptyProviderForm(), name: "x", baseUrl: url }).baseUrl).toMatch(message);
  });

  it("refuses keys with spaces and a local Anthropic", () => {
    const form = { ...emptyProviderForm(), name: "x", kind: "anthropic" as const, apiKey: "sk with space", local: true };
    expect(validateProviderForm(form)).toMatchObject({ apiKey: expect.any(String), local: expect.any(String) });
  });

  it("asks for the key again when the URL changes", () => {
    const moved = { ...formFromProvider(team), baseUrl: "https://elsewhere.example/v1" };
    expect(validateProviderForm(moved, team).apiKey).toMatch(/Enter the key again/);
    expect(validateProviderForm({ ...moved, apiKey: "sk-new-key" }, team)).toEqual({});
    expect(validateProviderForm({ ...moved, clearKey: true }, team)).toEqual({});
  });

  it("creates with the key only when one was typed", () => {
    const form = { ...emptyProviderForm(), name: " Local ", baseUrl: "http://gpu:11434/v1", local: true };
    expect(toProviderCreate(form)).toEqual({ name: "Local", kind: "openai_compatible", base_url: "http://gpu:11434/v1", local: true, enabled: true });
    expect(toProviderCreate({ ...form, apiKey: " sk-1 " }).api_key).toBe("sk-1");
  });

  it("sends only what changed; no key typed keeps the stored one", () => {
    const form = formFromProvider(team);
    expect(form.apiKey).toBe(""); // the key is never loaded into the form
    expect(toProviderPatch(form, team)).toEqual({});
    expect(toProviderPatch({ ...form, name: "Renamed" }, team)).toEqual({ name: "Renamed" });
    expect(toProviderPatch({ ...form, apiKey: "sk-new" }, team)).toEqual({ api_key: "sk-new" });
    expect(toProviderPatch({ ...form, clearKey: true }, team)).toEqual({ clear_key: true });
    expect(toProviderPatch({ ...form, baseUrl: "https://api.openai.com/v1/" }, team)).toEqual({});
  });
});

describe("model form", () => {
  it("needs a provider and a name, and both prices or neither", () => {
    expect(Object.keys(validateModelForm(emptyModelForm()))).toEqual(["providerId", "name"]);
    const half = { ...emptyModelForm("p-1"), name: "m", inputPrice: "1" };
    expect(validateModelForm(half).outputPrice).toMatch(/both prices/);
    expect(validateModelForm({ ...half, outputPrice: "abc" }).outputPrice).toMatch(/A price in dollars/);
    expect(validateModelForm({ ...half, outputPrice: "4" })).toEqual({});
  });

  it("maps to the API's shapes", () => {
    const form = { ...emptyModelForm("p-1"), name: " gpt-x ", inputPrice: "1.5", outputPrice: "6" };
    expect(toModelCreate(form)).toEqual({ provider_id: "p-1", name: "gpt-x", label: null, input_usd_per_mtok: 1.5, output_usd_per_mtok: 6, enabled: true });
    const original = catalog.models[1];
    expect(toModelPatch(formFromModel(original), original)).toEqual({});
    expect(toModelPatch({ ...formFromModel(original), inputPrice: "1", outputPrice: "2", enabled: false }, original)).toEqual({
      input_usd_per_mtok: 1,
      output_usd_per_mtok: 2,
      enabled: false,
    });
  });
});

describe("roles", () => {
  it("offers enabled models of enabled providers and marks the default", () => {
    const options = roleOptions(catalog, "answer");
    expect(options.map((o) => o.value)).toEqual([SONNET, "p-1:gpt-team", "p-2:qwen3:14b"]);
    expect(options[0].label).toBe("claude-sonnet-5-5 (Deployment default) · default");
    const disabled = { ...catalog, providers: [system, { ...team, enabled: false }, onPrem] };
    expect(roleOptions(disabled, "answer").map((o) => o.value)).not.toContain("p-1:gpt-team");
  });

  it("stores only the roles that differ from the defaults", () => {
    expect(rolesPayload({ ...roles(SONNET), judge: "p-1:gpt-team" }, roles(SONNET))).toEqual({
      answer: null,
      router: null,
      judge: "p-1:gpt-team",
      generator: null,
      agent: null,
    });
  });
});

describe("data policy", () => {
  it("lists the labels kept away from external models", () => {
    expect(levelsAbove("confidential")).toEqual(["restricted"]);
    expect(levelsAbove("restricted")).toEqual([]);
    expect(levelsAbove("public")).toEqual(["internal", "confidential", "restricted"]);
  });

  it("offers only local models for the rest", () => {
    expect(localModelOptions(catalog)).toEqual([{ value: "p-2:qwen3:14b", label: "Qwen 3 (On-prem)" }]);
  });

  it("explains where data goes in plain words", () => {
    const [out, rest, permissions] = policyExplanation(catalog, { max_external_sensitivity: "internal", local_model: null });
    expect(out).toBe(
      "Retrieved sources labelled public and internal may be sent to external models (Deployment default and Team OpenAI), that is, to servers outside your infrastructure.",
    );
    expect(rest).toBe("Confidential and restricted sources are left out of answers from external models, and people are told how many were left out.");
    expect(permissions).toMatch(/only ever sees sources the person asking may read/);
    const local = policyExplanation(catalog, { max_external_sensitivity: "confidential", local_model: "p-2:qwen3:14b" });
    expect(local[1]).toBe("Questions that involve restricted sources are answered by Qwen 3 (On-prem) instead, on your own infrastructure.");
    expect(policyExplanation(catalog, { max_external_sensitivity: "restricted", local_model: null })[1]).toMatch(/Nothing is held back/);
  });
});

describe("provider test results", () => {
  it("reads as one line", () => {
    expect(probeSummary({ ok: true, latency_ms: 412, error: null, model: "gpt-team" })).toBe("Works: gpt-team answered in 412 ms.");
    expect(probeSummary({ ok: false, latency_ms: 90, error: "The server refused the API key (HTTP 401).", model: "x" })).toBe(
      "The server refused the API key (HTTP 401).",
    );
  });
});
