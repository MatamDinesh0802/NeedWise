import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import type { CatalogModel, ModelCatalog, ModelProvider, RoleModels } from "@/lib/admin/models";

import { CatalogPanel, ModelsView, PolicyPanel, ProviderCard, RolesPanel } from "./models-section";

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
const sonnet: CatalogModel = {
  id: SONNET,
  provider_id: "system",
  name: "claude-sonnet-5-5",
  label: "claude-sonnet-5-5",
  local: false,
  input_usd_per_mtok: 2,
  output_usd_per_mtok: 10,
  enabled: true,
};
const roles = (id: string): RoleModels => ({ answer: id, router: id, judge: id, generator: id, agent: id });
const catalog = (overrides: Partial<ModelCatalog> = {}): ModelCatalog => ({
  providers: [system, team],
  models: [sonnet, { ...sonnet, id: "p-1:gpt-team", provider_id: "p-1", name: "gpt-team", label: "GPT team", input_usd_per_mtok: null, output_usd_per_mtok: null }],
  roles: roles(SONNET),
  policy: { max_external_sensitivity: "confidential", local_model: null },
  defaults: { roles: roles(SONNET), policy: { max_external_sensitivity: "confidential", local_model: null } },
  ...overrides,
});

describe("ModelsView", () => {
  it("shows a labelled loading state, then an error with a retry", () => {
    expect(renderToStaticMarkup(<ModelsView state={{ status: "loading" }} />)).toContain('aria-busy="true"');
    const error = new ApiError({ status: 403, title: "Not allowed", code: "forbidden" });
    const html = renderToStaticMarkup(<ModelsView state={{ status: "error", error, refreshing: false }} onRetry={() => {}} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("Retry");
  });

  it("renders every panel for a loaded catalog", () => {
    const html = renderToStaticMarkup(<ModelsView state={{ status: "success", data: catalog(), refreshing: false }} />);
    for (const heading of ["Providers", "Model catalog", "Which model does what", "Data policy for external models"]) {
      expect(html).toContain(heading);
    }
  });
});

describe("ProviderCard", () => {
  it("shows the deployment's provider as read-only, without its key or a delete button", () => {
    const html = renderToStaticMarkup(<ProviderCard provider={system} models={2} result={null} />);
    expect(html).toContain("Deployment · read-only");
    expect(html).toContain("Set by the server&#x27;s configuration");
    expect(html).toContain('aria-label="Test Deployment default"');
    expect(html).not.toContain("Delete");
    expect(html).not.toContain("Edit");
  });

  it("shows only the hint of an organization's key, and the test result for screen readers", () => {
    const html = renderToStaticMarkup(
      <ProviderCard provider={team} models={1} result={{ ok: false, latency_ms: 80, error: "The server refused the API key (HTTP 401).", model: "gpt-team" }} />,
    );
    expect(html).toContain("Stored, ends in a1b2");
    expect(html).toContain("External: leaves your infrastructure");
    expect(html).toMatch(/aria-live="polite" id="probe-p-1"><p[^>]*>.*The server refused the API key \(HTTP 401\)\./);
    expect(html).toContain('aria-label="Delete Team OpenAI"');
    expect(html).toContain('aria-label="Disable Team OpenAI"');
  });
});

describe("CatalogPanel", () => {
  it("lists prices, marks deployment models, and offers actions only on the organization's", () => {
    const html = renderToStaticMarkup(<CatalogPanel catalog={catalog()} />);
    expect(html).toContain("$2.00 in · $10.00 out per million tokens");
    expect(html).toContain("No price: costs show as unknown");
    expect(html).toContain('aria-label="Edit GPT team"');
    expect(html).not.toContain('aria-label="Edit claude-sonnet-5-5"');
    expect(html).toContain('<span class="sr-only">Actions</span>');
  });

  it("explains the empty catalog and needs a provider before a model", () => {
    const html = renderToStaticMarkup(<CatalogPanel catalog={catalog({ providers: [system], models: [sonnet] })} />);
    expect(html).toContain("No models of your own yet");
    expect(html).toContain("Add a provider, then the models you want to use from it.");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*title="Add a provider first"/);
  });
});

describe("RolesPanel", () => {
  it("has a labelled select per role and nothing to save until something changes", () => {
    const html = renderToStaticMarkup(<RolesPanel catalog={catalog()} />);
    for (const label of ["Answers", "Routing and rewriting", "Agents", "Extraction", "Evaluation judge"]) {
      expect(html).toMatch(new RegExp(`<label[^>]*for="([^"]+)"[^>]*>${label}</label>`));
    }
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled=""[^>]*>Save roles<\/button>/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Use the deployment&#x27;s defaults<\/button>/);
  });
});

describe("PolicyPanel", () => {
  it("says in plain words where data goes", () => {
    const html = renderToStaticMarkup(<PolicyPanel catalog={catalog()} />);
    expect(html).toContain("Where your data goes");
    expect(html).toContain("Retrieved sources labelled public, internal and confidential may be sent to external models (Deployment default and Team OpenAI)");
    expect(html).toContain("Restricted sources are left out of answers from external models");
    expect(html).toContain("No local models yet");
    expect(html).toContain("Not covered yet");
  });
});
