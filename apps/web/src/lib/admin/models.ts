/**
 * Admin > Models (/api/v1/admin/models): providers with write-only keys, the model catalog
 * with prices, which model serves each role, and the data policy for external models.
 * Shapes mirror apps/api/src/needwise/api/v1/model_catalog.py. Pure parts are unit-tested.
 */

import { apiJson } from "@/lib/api";

export type ProviderKind = "anthropic" | "openai_compatible";
export type Sensitivity = "public" | "internal" | "confidential" | "restricted";
export type ModelRole = "answer" | "router" | "judge" | "generator" | "agent";

export interface ModelProvider {
  /** "system" (and "system-local") for the deployment's own; a uuid for the organization's. */
  id: string;
  name: string;
  kind: ProviderKind;
  base_url: string | null;
  local: boolean;
  has_key: boolean;
  /** Like "…a1b2". The key itself is never sent back. */
  key_hint: string | null;
  enabled: boolean;
  system: boolean;
}

export interface CatalogModel {
  /** "<provider id>:<model name>", stable: agents store it. */
  id: string;
  provider_id: string;
  name: string;
  label: string;
  local: boolean;
  input_usd_per_mtok: number | null;
  output_usd_per_mtok: number | null;
  enabled: boolean;
}

export type RoleModels = Record<ModelRole, string>;

export interface DataPolicy {
  max_external_sensitivity: Sensitivity;
  local_model: string | null;
}

export interface ModelCatalog {
  providers: ModelProvider[];
  models: CatalogModel[];
  roles: RoleModels;
  policy: DataPolicy;
  defaults: { roles: RoleModels; policy: DataPolicy };
}

export interface ProbeResult {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
  model: string | null;
}

export interface ProviderCreate {
  name: string;
  kind: ProviderKind;
  base_url: string | null;
  api_key?: string;
  local: boolean;
  enabled: boolean;
}

export type ProviderPatch = Partial<Omit<ProviderCreate, "kind">> & { clear_key?: boolean };

export interface ModelCreate {
  provider_id: string;
  name: string;
  label: string | null;
  input_usd_per_mtok: number | null;
  output_usd_per_mtok: number | null;
  enabled: boolean;
}

export type ModelPatch = Partial<Omit<ModelCreate, "provider_id" | "name">>;

const BASE = "/api/v1/admin/models";
export const modelsKey = "admin/models";

/** Catalog ids contain ":" and often "/" (e.g. "…:meta-llama/Llama-4"): encode each segment. */
export function catalogModelPath(id: string): string {
  return `${BASE}/catalog/${id.split("/").map(encodeURIComponent).join("/")}`;
}

export const modelsApi = {
  get: () => apiJson<ModelCatalog>(BASE),
  createProvider: (body: ProviderCreate) => apiJson<ModelProvider>(`${BASE}/providers`, { method: "POST", json: body }),
  updateProvider: (id: string, body: ProviderPatch) =>
    apiJson<ModelProvider>(`${BASE}/providers/${encodeURIComponent(id)}`, { method: "PATCH", json: body }),
  deleteProvider: (id: string) => apiJson<void>(`${BASE}/providers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  testProvider: (id: string, model?: string) =>
    apiJson<ProbeResult>(`${BASE}/providers/${encodeURIComponent(id)}/test`, {
      method: "POST",
      json: model ? { model } : {},
    }),
  createModel: (body: ModelCreate) => apiJson<CatalogModel>(`${BASE}/catalog`, { method: "POST", json: body }),
  updateModel: (id: string, body: ModelPatch) => apiJson<CatalogModel>(catalogModelPath(id), { method: "PATCH", json: body }),
  deleteModel: (id: string) => apiJson<void>(catalogModelPath(id), { method: "DELETE" }),
  /** null for a role: the deployment's default. */
  putRoles: (roles: Record<ModelRole, string | null>) => apiJson<ModelCatalog>(`${BASE}/roles`, { method: "PUT", json: roles }),
  putPolicy: (policy: DataPolicy) => apiJson<ModelCatalog>(`${BASE}/policy`, { method: "PUT", json: policy }),
};

// --- Words -----------------------------------------------------------------------------------

export const KIND_LABELS: Record<ProviderKind, string> = {
  anthropic: "Anthropic (Claude)",
  openai_compatible: "OpenAI-compatible",
};

export const KIND_HINTS: Record<ProviderKind, string> = {
  anthropic: "Your own Anthropic API key. Leave the URL empty for api.anthropic.com.",
  openai_compatible:
    "Any /v1/chat/completions server: OpenAI, Azure-style gateways, vLLM, Ollama, llama.cpp, LM Studio. The URL usually ends in /v1.",
};

export const ROLES: readonly { role: ModelRole; label: string; description: string }[] = [
  { role: "answer", label: "Answers", description: "Writes cited answers from your documents." },
  { role: "router", label: "Routing and rewriting", description: "Rewrites follow-up questions and plans which agents to ask." },
  { role: "agent", label: "Agents", description: "Department, research and analytics agents that use tools." },
  { role: "generator", label: "Extraction", description: "Meeting notes and other structured extraction." },
  { role: "judge", label: "Evaluation judge", description: "Grades answers in evaluations (nw eval)." },
];

export const SENSITIVITY_LABELS: Record<Sensitivity, string> = {
  public: "Public",
  internal: "Internal",
  confidential: "Confidential",
  restricted: "Restricted",
};

export const SENSITIVITY_OPTIONS: readonly { value: Sensitivity; label: string }[] = [
  { value: "public", label: "Public only: nothing labelled internal or above leaves" },
  { value: "internal", label: "Up to internal" },
  { value: "confidential", label: "Up to confidential (restricted stays in)" },
  { value: "restricted", label: "Everything, restricted included" },
];

const LEVELS: readonly Sensitivity[] = ["public", "internal", "confidential", "restricted"];

/** The labels above `threshold`: what never goes to an external model. */
export function levelsAbove(threshold: Sensitivity): Sensitivity[] {
  return LEVELS.slice(LEVELS.indexOf(threshold) + 1);
}

function listWords(words: string[]): string {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 });

/** "$2.00 in · $10.00 out per million tokens", or that there is no price (no cost is shown). */
export function formatPrice(model: Pick<CatalogModel, "input_usd_per_mtok" | "output_usd_per_mtok">): string {
  const { input_usd_per_mtok: input, output_usd_per_mtok: output } = model;
  if (input == null || output == null) return "No price: costs show as unknown";
  return `${usd.format(input)} in · ${usd.format(output)} out per million tokens`;
}

export function providerName(catalog: ModelCatalog, providerId: string): string {
  return catalog.providers.find((p) => p.id === providerId)?.name ?? providerId;
}

export function modelLabel(catalog: ModelCatalog, modelId: string | null): string {
  if (!modelId) return "none";
  const model = catalog.models.find((m) => m.id === modelId);
  return model ? `${model.label} (${providerName(catalog, model.provider_id)})` : modelId;
}

/** Models a role can use: enabled, from an enabled provider. The default is marked. */
export function roleOptions(catalog: ModelCatalog, role: ModelRole): { value: string; label: string }[] {
  const enabled = new Set(catalog.providers.filter((p) => p.enabled).map((p) => p.id));
  return catalog.models
    .filter((m) => m.enabled && enabled.has(m.provider_id))
    .map((m) => ({
      value: m.id,
      label: `${modelLabel(catalog, m.id)}${m.id === catalog.defaults.roles[role] ? " · default" : ""}`,
    }));
}

/** What PUT /roles should get: null where the choice equals the default, so the role keeps
 * following the deployment if its default changes. */
export function rolesPayload(chosen: RoleModels, defaults: RoleModels): Record<ModelRole, string | null> {
  return Object.fromEntries(
    ROLES.map(({ role }) => [role, chosen[role] === defaults[role] ? null : chosen[role]]),
  ) as Record<ModelRole, string | null>;
}

/** Models that may receive sources above the threshold: on local providers only. */
export function localModelOptions(catalog: ModelCatalog): { value: string; label: string }[] {
  const local = new Set(catalog.providers.filter((p) => p.enabled && p.local).map((p) => p.id));
  return catalog.models.filter((m) => m.enabled && local.has(m.provider_id)).map((m) => ({ value: m.id, label: modelLabel(catalog, m.id) }));
}

/** Plain words on where data goes under this policy. */
export function policyExplanation(catalog: ModelCatalog, policy: DataPolicy): string[] {
  const above = levelsAbove(policy.max_external_sensitivity).map((l) => SENSITIVITY_LABELS[l].toLowerCase());
  const allowed = LEVELS.slice(0, LEVELS.indexOf(policy.max_external_sensitivity) + 1).map((l) => SENSITIVITY_LABELS[l].toLowerCase());
  const external = catalog.providers.filter((p) => !p.local).map((p) => p.name);
  const lines = [
    `Retrieved sources labelled ${listWords(allowed)} may be sent to external models${external.length ? ` (${listWords(external)})` : ""}, that is, to servers outside your infrastructure.`,
  ];
  if (above.length === 0) {
    lines.push("Nothing is held back: every source someone may read can reach an external model.");
  } else if (policy.local_model) {
    lines.push(`Questions that involve ${listWords(above)} sources are answered by ${modelLabel(catalog, policy.local_model)} instead, on your own infrastructure.`);
  } else {
    lines.push(`${listWords(above).replace(/^./, (c) => c.toUpperCase())} sources are left out of answers from external models, and people are told how many were left out.`);
  }
  lines.push("Permissions come first either way: a model only ever sees sources the person asking may read.");
  return lines;
}

// --- Provider form ---------------------------------------------------------------------------

export interface ProviderForm {
  name: string;
  kind: ProviderKind;
  baseUrl: string;
  /** Write-only: empty means "keep the stored key" when editing. */
  apiKey: string;
  clearKey: boolean;
  local: boolean;
  enabled: boolean;
}

export type ProviderFormErrors = Partial<Record<"name" | "baseUrl" | "apiKey" | "local", string>>;

export function emptyProviderForm(): ProviderForm {
  return { name: "", kind: "openai_compatible", baseUrl: "", apiKey: "", clearKey: false, local: false, enabled: true };
}

export function formFromProvider(provider: ModelProvider): ProviderForm {
  return {
    name: provider.name,
    kind: provider.kind,
    baseUrl: provider.base_url ?? "",
    apiKey: "",
    clearKey: false,
    local: provider.local,
    enabled: provider.enabled,
  };
}

const METADATA = /^(169\.254\.|\[?fe80:|metadata\.google\.internal)/i;

export function validateProviderForm(form: ProviderForm, original: ModelProvider | null = null): ProviderFormErrors {
  const errors: ProviderFormErrors = {};
  if (!form.name.trim()) errors.name = "Give the provider a name.";
  else if (form.name.trim().length > 100) errors.name = "At most 100 characters.";
  const url = form.baseUrl.trim();
  if (!url) {
    if (form.kind === "openai_compatible") errors.baseUrl = "Enter the server's URL, for example https://api.openai.com/v1.";
  } else {
    let parsed: URL | null = null;
    try {
      parsed = new URL(url);
    } catch {
      errors.baseUrl = "Enter a full URL, starting with https:// or http://.";
    }
    if (parsed) {
      if (parsed.protocol !== "https:" && parsed.protocol !== "http:") errors.baseUrl = "Only http:// and https:// URLs.";
      else if (parsed.username || parsed.password) errors.baseUrl = "Put the key in the API key field, not in the URL.";
      else if (parsed.search || parsed.hash) errors.baseUrl = "The URL cannot have a query (?…) or a fragment (#…).";
      else if (METADATA.test(parsed.hostname)) errors.baseUrl = "Cloud metadata and link-local addresses are not allowed.";
    }
  }
  const key = form.apiKey.trim();
  if (key && (/\s/.test(key) || key.length > 500)) errors.apiKey = "One line, no spaces, at most 500 characters.";
  if (original?.has_key && !key && !form.clearKey && url.replace(/\/$/, "") !== (original.base_url ?? "")) {
    errors.apiKey = "Enter the key again: a key is only sent to the URL it was saved for.";
  }
  if (form.kind === "anthropic" && form.local) errors.local = "Anthropic's API is never local.";
  return errors;
}

export function toProviderCreate(form: ProviderForm): ProviderCreate {
  const key = form.apiKey.trim();
  return {
    name: form.name.trim(),
    kind: form.kind,
    base_url: form.baseUrl.trim() || null,
    ...(key ? { api_key: key } : {}),
    local: form.kind === "openai_compatible" && form.local,
    enabled: form.enabled,
  };
}

/** Only what changed. No key typed: the stored key is kept (never sent back to us to resend). */
export function toProviderPatch(form: ProviderForm, original: ModelProvider): ProviderPatch {
  const patch: ProviderPatch = {};
  if (form.name.trim() !== original.name) patch.name = form.name.trim();
  const url = form.baseUrl.trim() || null;
  if ((url ?? "").replace(/\/$/, "") !== (original.base_url ?? "")) patch.base_url = url;
  if (form.local !== original.local) patch.local = form.local;
  if (form.enabled !== original.enabled) patch.enabled = form.enabled;
  const key = form.apiKey.trim();
  if (key) patch.api_key = key;
  else if (form.clearKey && original.has_key) patch.clear_key = true;
  return patch;
}

// --- Model form ------------------------------------------------------------------------------

export interface ModelForm {
  providerId: string;
  name: string;
  label: string;
  inputPrice: string;
  outputPrice: string;
  enabled: boolean;
}

export type ModelFormErrors = Partial<Record<keyof ModelForm, string>>;

export function emptyModelForm(providerId = ""): ModelForm {
  return { providerId, name: "", label: "", inputPrice: "", outputPrice: "", enabled: true };
}

export function formFromModel(model: CatalogModel): ModelForm {
  return {
    providerId: model.provider_id,
    name: model.name,
    label: model.label,
    inputPrice: model.input_usd_per_mtok?.toString() ?? "",
    outputPrice: model.output_usd_per_mtok?.toString() ?? "",
    enabled: model.enabled,
  };
}

/** "" → null (no price); otherwise a number of dollars, or NaN when it is not one. */
export function parsePrice(value: string): number | null {
  const text = value.trim().replace(/^\$/, "");
  if (!text) return null;
  return /^\d+(\.\d+)?$/.test(text) ? Number(text) : Number.NaN;
}

export function validateModelForm(form: ModelForm): ModelFormErrors {
  const errors: ModelFormErrors = {};
  if (!form.providerId) errors.providerId = "Choose a provider.";
  if (!form.name.trim()) errors.name = "Enter the model's name as the provider knows it.";
  else if (form.name.trim().length > 200) errors.name = "At most 200 characters.";
  if (form.label.trim().length > 200) errors.label = "At most 200 characters.";
  for (const field of ["inputPrice", "outputPrice"] as const) {
    const price = parsePrice(form[field]);
    if (price !== null && (Number.isNaN(price) || price > 10_000)) errors[field] = "A price in dollars, like 2.50, up to 10000.";
  }
  if ((parsePrice(form.inputPrice) === null) !== (parsePrice(form.outputPrice) === null)) {
    const missing = parsePrice(form.inputPrice) === null ? "inputPrice" : "outputPrice";
    errors[missing] ??= "Give both prices, or neither (then costs show as unknown).";
  }
  return errors;
}

export function toModelCreate(form: ModelForm): ModelCreate {
  return {
    provider_id: form.providerId,
    name: form.name.trim(),
    label: form.label.trim() || null,
    input_usd_per_mtok: parsePrice(form.inputPrice),
    output_usd_per_mtok: parsePrice(form.outputPrice),
    enabled: form.enabled,
  };
}

export function toModelPatch(form: ModelForm, original: CatalogModel): ModelPatch {
  const patch: ModelPatch = {};
  const label = form.label.trim() || original.name;
  if (label !== original.label) patch.label = label;
  const input = parsePrice(form.inputPrice);
  const output = parsePrice(form.outputPrice);
  if (input !== original.input_usd_per_mtok) patch.input_usd_per_mtok = input;
  if (output !== original.output_usd_per_mtok) patch.output_usd_per_mtok = output;
  if (form.enabled !== original.enabled) patch.enabled = form.enabled;
  return patch;
}

export function deleteProviderText(provider: ModelProvider, models: number): { title: string; description: string; confirm: string } {
  return {
    title: `Delete ${provider.name}?`,
    description: `This removes the provider, its stored key and its ${models} ${models === 1 ? "model" : "models"} from the catalog. If a role, the data policy or an agent still uses one of them, nothing is deleted and you are told where it is used. This can't be undone.`,
    confirm: "Delete provider",
  };
}

/** The result of a provider test, in one line. */
export function probeSummary(result: ProbeResult): string {
  if (result.ok) return `Works: ${result.model ?? "the model"} answered in ${result.latency_ms ?? "?"} ms.`;
  return result.error ?? "The test failed.";
}
