/**
 * Department agents (/api/v1/admin/agents): types, API, and the form ↔ payload mapping, plus the
 * pure parts of the tools picker, model choice, versions diff and test console.
 * Shapes mirror apps/api/src/workmind/api/v1/agents.py. Pure parts are unit-tested.
 */

import { ApiError, apiJson } from "@/lib/api";
import { modelsKey, type ModelCatalog } from "@/lib/admin/models";
import { isValidSlug, suggestSlug } from "@/lib/slug";

export interface Agent {
  id: string;
  key: string;
  name: string;
  description: string;
  instructions: string;
  collections: string[];
  examples: string[];
  /** Read-level tools it may call besides searching; they run as the person asking. */
  tools: string[];
  /** A model catalog id ("system:claude-sonnet-5-5"), or null: the default of its role. */
  model: string | null;
  max_searches: number;
  max_tool_calls: number;
  max_output_tokens: number;
  enabled: boolean;
  version: number;
  updated_at: string;
}

export type AgentFields = Omit<Agent, "id" | "key" | "version" | "updated_at">;
/** A save: the fields plus an optional "what changed" note for the version history. */
export type AgentUpdate = AgentFields & { note?: string };
export type AgentCreate = AgentUpdate & { key: string };
/** What a version stores (older versions may lack newer fields). */
export type AgentSnapshot = Partial<AgentFields> & { key?: string };

export const AGENT_LIMITS = {
  keyMax: 40,
  nameMax: 80,
  descriptionMin: 10,
  descriptionMax: 1000,
  instructionsMax: 2000,
  examplesMax: 10,
  exampleMax: 300,
  searchesMin: 1,
  searchesMax: 5,
  toolsMax: 10,
  toolCallsMin: 0,
  toolCallsMax: 5,
  outputTokensMin: 1000,
  outputTokensMax: 16000,
  noteMax: 500,
  questionMax: 2000,
} as const;

export const DEFAULT_OUTPUT_TOKENS = 6000;
/** Offered in the limits select; the API accepts any value in range. */
export const OUTPUT_TOKEN_CHOICES = [2000, 4000, 6000, 8000, 12000, 16000] as const;
/** Tool calls an agent gets when its first tool is chosen. */
export const DEFAULT_TOOL_CALLS = 2;

const BASE = "/api/v1/admin/agents";

export interface AgentTool {
  name: string;
  description: string;
  side_effect: "read" | "write" | "external" | string;
  scope: string;
  source: string;
  for_agents: boolean;
  not_for_agents_reason: string | null;
}

export interface AgentVersion {
  version: number;
  author: { id: string; name: string } | null;
  note: string;
  created_at: string;
  current: boolean;
  snapshot: AgentSnapshot;
}

export interface AgentVersions {
  current: number;
  items: AgentVersion[];
}

/** An unsaved definition for the test console. */
export type AgentDraft = AgentFields & { key?: string };

export interface AgentTestRequest {
  question: string;
  draft?: AgentDraft;
  /** The saved agent the draft replaces (or, without a draft, the one to test). */
  key?: string;
}

export interface AgentTestRouting {
  mode: "direct" | "single" | "fan_out";
  agents: { key: string; name: string; goal: string }[];
  routed_by: "fast_path" | "planner" | "fallback";
  reason: string | null;
  tested_agent_picked: boolean | null;
}

export interface AgentTestCitation {
  marker: string;
  document_id: string;
  title: string;
  filename: string;
  section: string;
  page: number | null;
  trust: string;
}

export interface AgentTestUsage {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  models: string[];
}

export interface AgentTestStep {
  kind: "search" | "tool" | string;
  detail: string;
  ok: boolean;
  error: string | null;
}

export interface AgentTestAnswer {
  agent: string;
  goal: string;
  text: string;
  citations: AgentTestCitation[];
  usage: AgentTestUsage;
  steps: AgentTestStep[];
  abstained: boolean;
  withheld: number;
  error: string | null;
}

export interface AgentTestResult {
  routing: AgentTestRouting;
  answer: AgentTestAnswer | null;
}

export const agentsApi = {
  list: () => apiJson<{ items: Agent[] }>(BASE),
  create: (body: AgentCreate) => apiJson<Agent>(BASE, { method: "POST", json: body }),
  update: (key: string, body: AgentUpdate) =>
    apiJson<Agent>(`${BASE}/${encodeURIComponent(key)}`, { method: "PUT", json: body }),
  /** 204. The planner stops routing to it on the next question. */
  remove: (key: string) => apiJson<void>(`${BASE}/${encodeURIComponent(key)}`, { method: "DELETE" }),
  versions: (key: string) => apiJson<AgentVersions>(`${BASE}/${encodeURIComponent(key)}/versions`),
  /** A new version equal to version `n`; history is never rewritten. */
  restore: (key: string, n: number, note = "") =>
    apiJson<Agent>(`${BASE}/${encodeURIComponent(key)}/versions/${n}/restore`, { method: "POST", json: { note } }),
  /** Routing and an answer for one question, as you, with your permissions. Stored nowhere. */
  test: (body: AgentTestRequest) => apiJson<AgentTestResult>(`${BASE}/test`, { method: "POST", json: body }),
  tools: () => apiJson<AgentTool[]>("/api/v1/admin/tools"),
};

export const agentsKey = "admin/agents";
export const agentToolsKey = "admin/tools";
export const agentVersionsKey = (key: string) => `admin/agents/${key}/versions`;

// --- Model catalog (GET /api/v1/admin/models; types and query key from Admin > Models) ------

export type { ModelCatalog };

/** The same query as Admin > Models, so a model added there shows up here. */
export const modelCatalogKey = modelsKey;

/** The tenant's model catalog, or null where this deployment has none (404). */
export async function fetchModelCatalog(): Promise<ModelCatalog | null> {
  try {
    return await apiJson<ModelCatalog>("/api/v1/admin/models");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

/** The select's value for "no model of its own". */
export const DEFAULT_MODEL = "__default__";

export interface ModelOption {
  value: string;
  label: string;
  disabled?: boolean;
}

/**
 * "Default for its role" first, then the enabled models of enabled providers. A saved model
 * the catalog no longer offers stays selectable (marked), so opening and saving an agent never
 * changes its model silently.
 */
export function modelOptions(catalog: ModelCatalog | null, current: string | null): ModelOption[] {
  const providers = new Map((catalog?.providers ?? []).map((p) => [p.id, p]));
  const models = new Map((catalog?.models ?? []).map((m) => [m.id, m]));
  const roleDefault = catalog?.roles.agent ? models.get(catalog.roles.agent) : undefined;
  const options: ModelOption[] = [
    { value: DEFAULT_MODEL, label: roleDefault ? `Default for its role (${roleDefault.label})` : "Default for its role" },
  ];
  for (const model of catalog?.models ?? []) {
    const provider = providers.get(model.provider_id);
    if (!model.enabled || (provider && !provider.enabled)) continue;
    const where = model.local ? "local" : (provider?.name ?? model.provider_id);
    options.push({ value: model.id, label: `${model.label} · ${where}` });
  }
  if (current && !options.some((o) => o.value === current)) {
    options.push({ value: current, label: `${current} (not in the model catalog)` });
  }
  return options;
}

export function modelLabel(catalog: ModelCatalog | null, model: string | null): string {
  if (!model) return "Default model";
  return catalog?.models.find((m) => m.id === model)?.label ?? model;
}

// --- Tools ---------------------------------------------------------------------------------

/** Tools an agent may have first (by name), then the ones it may not, with the reason. */
export function sortTools(tools: readonly AgentTool[]): AgentTool[] {
  return [...tools].sort((a, b) => Number(b.for_agents) - Number(a.for_agents) || a.name.localeCompare(b.name));
}

export const SIDE_EFFECT_LABEL: Record<string, string> = {
  read: "Reads only",
  write: "Changes data",
  external: "Leaves WorkMind",
};

// --- Deleting ------------------------------------------------------------------------------

/** The confirmation names the agent and its key, so the wrong one is hard to delete. */
export function deleteAgentText(agent: Pick<Agent, "name" | "key">): { title: string; description: string; confirm: string } {
  return {
    title: `Delete ${agent.name}?`,
    description: `The planner stops sending questions to “${agent.name}” (${agent.key}) from the next question on. Its collections and their documents are not touched, and its version history is deleted with it. To pause it instead, disable it. This can't be undone.`,
    confirm: "Delete agent",
  };
}

// --- Form ----------------------------------------------------------------------------------

export interface AgentForm {
  key: string;
  /** True once the admin typed a key; until then it follows the name. */
  keyEdited: boolean;
  name: string;
  description: string;
  instructions: string;
  collections: string[];
  /** Editable rows; blanks are dropped on save. */
  examples: string[];
  tools: string[];
  /** DEFAULT_MODEL, or a catalog model id. */
  model: string;
  maxSearches: number;
  maxToolCalls: number;
  maxOutputTokens: number;
  enabled: boolean;
  /** "What changed", stored with the new version. */
  note: string;
}

export function emptyAgentForm(): AgentForm {
  return {
    key: "",
    keyEdited: false,
    name: "",
    description: "",
    instructions: "",
    collections: [],
    examples: [""],
    tools: [],
    model: DEFAULT_MODEL,
    maxSearches: 2,
    maxToolCalls: 0,
    maxOutputTokens: DEFAULT_OUTPUT_TOKENS,
    enabled: true,
    note: "",
  };
}

export function formFromAgent(agent: Agent): AgentForm {
  return {
    key: agent.key,
    keyEdited: true,
    name: agent.name,
    description: agent.description,
    instructions: agent.instructions,
    collections: [...agent.collections],
    examples: agent.examples.length ? [...agent.examples] : [""],
    tools: [...agent.tools],
    model: agent.model ?? DEFAULT_MODEL,
    maxSearches: agent.max_searches,
    maxToolCalls: agent.max_tool_calls,
    maxOutputTokens: agent.max_output_tokens,
    enabled: agent.enabled,
    note: "",
  };
}

/**
 * Keys are slugs, at most 40 characters: "Customer Support" → "customer-support". A trailing
 * "agent" is left out, as in the seeded agents ("HR agent" is `hr`): "Legal agent" → "legal".
 */
export function suggestAgentKey(name: string): string {
  const slug = suggestSlug(name).replace(/(.)-agent$/, "$1");
  return slug.slice(0, AGENT_LIMITS.keyMax).replace(/-+$/, "");
}

/** Name change, keeping the key in sync until it was edited by hand. */
export function withName(form: AgentForm, name: string): AgentForm {
  return { ...form, name, key: form.keyEdited ? form.key : suggestAgentKey(name) };
}

/** Tool selection: the first tool brings a call budget, removing the last one takes it away. */
export function withTools(form: AgentForm, tools: string[]): AgentForm {
  const unique = [...new Set(tools)];
  let maxToolCalls = form.maxToolCalls;
  if (unique.length === 0) maxToolCalls = 0;
  else if (maxToolCalls === 0) maxToolCalls = DEFAULT_TOOL_CALLS;
  return { ...form, tools: unique, maxToolCalls };
}

/** Trimmed, non-empty, deduplicated examples in their original order. */
export function cleanExamples(examples: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of examples) {
    const example = raw.trim();
    const norm = example.toLowerCase();
    if (example && !seen.has(norm)) {
      seen.add(norm);
      out.push(example);
    }
  }
  return out;
}

export type AgentFormErrors = Partial<
  Record<
    | "key"
    | "name"
    | "description"
    | "instructions"
    | "collections"
    | "examples"
    | "maxSearches"
    | "tools"
    | "maxToolCalls"
    | "maxOutputTokens"
    | "note",
    string
  >
>;

function inRange(value: number, min: number, max: number): boolean {
  return Number.isInteger(value) && value >= min && value <= max;
}

export function validateAgentForm(form: AgentForm, { creating }: { creating: boolean }): AgentFormErrors {
  const errors: AgentFormErrors = {};
  const name = form.name.trim();
  const description = form.description.trim();
  if (!name) errors.name = "Give the agent a name.";
  else if (name.length > AGENT_LIMITS.nameMax) errors.name = `Use at most ${AGENT_LIMITS.nameMax} characters.`;
  if (creating) {
    if (!form.key) errors.key = "Enter a key.";
    else if (form.key.length > AGENT_LIMITS.keyMax) errors.key = `Use at most ${AGENT_LIMITS.keyMax} characters.`;
    else if (!isValidSlug(form.key)) errors.key = "Use only lowercase letters, numbers and single hyphens.";
  }
  if (description.length < AGENT_LIMITS.descriptionMin) {
    errors.description = `Describe what it can answer in at least ${AGENT_LIMITS.descriptionMin} characters; the planner reads this.`;
  } else if (description.length > AGENT_LIMITS.descriptionMax) {
    errors.description = `Use at most ${AGENT_LIMITS.descriptionMax} characters.`;
  }
  if (form.instructions.trim().length > AGENT_LIMITS.instructionsMax) {
    errors.instructions = `Use at most ${AGENT_LIMITS.instructionsMax} characters.`;
  }
  if (form.collections.length === 0) errors.collections = "Choose at least one collection to search.";
  const examples = cleanExamples(form.examples);
  if (examples.length > AGENT_LIMITS.examplesMax) errors.examples = `Use at most ${AGENT_LIMITS.examplesMax} examples.`;
  else if (examples.some((e) => e.length > AGENT_LIMITS.exampleMax)) {
    errors.examples = `Keep each example under ${AGENT_LIMITS.exampleMax} characters.`;
  }
  if (!inRange(form.maxSearches, AGENT_LIMITS.searchesMin, AGENT_LIMITS.searchesMax)) {
    errors.maxSearches = `Choose between ${AGENT_LIMITS.searchesMin} and ${AGENT_LIMITS.searchesMax} searches.`;
  }
  if (form.tools.length > AGENT_LIMITS.toolsMax) errors.tools = `Choose at most ${AGENT_LIMITS.toolsMax} tools.`;
  if (!inRange(form.maxToolCalls, AGENT_LIMITS.toolCallsMin, AGENT_LIMITS.toolCallsMax)) {
    errors.maxToolCalls = `Choose between ${AGENT_LIMITS.toolCallsMin} and ${AGENT_LIMITS.toolCallsMax} tool calls.`;
  } else if (form.tools.length > 0 && form.maxToolCalls === 0) {
    errors.maxToolCalls = "Allow at least one tool call, or remove the tools.";
  }
  if (!inRange(form.maxOutputTokens, AGENT_LIMITS.outputTokensMin, AGENT_LIMITS.outputTokensMax)) {
    errors.maxOutputTokens = `Choose between ${AGENT_LIMITS.outputTokensMin} and ${AGENT_LIMITS.outputTokensMax} tokens.`;
  }
  if (form.note.trim().length > AGENT_LIMITS.noteMax) errors.note = `Use at most ${AGENT_LIMITS.noteMax} characters.`;
  return errors;
}

export function toAgentFields(form: AgentForm): AgentFields {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    instructions: form.instructions.trim(),
    collections: [...new Set(form.collections)],
    examples: cleanExamples(form.examples),
    tools: [...new Set(form.tools)],
    model: form.model === DEFAULT_MODEL ? null : form.model,
    max_searches: form.maxSearches,
    max_tool_calls: form.maxToolCalls,
    max_output_tokens: form.maxOutputTokens,
    enabled: form.enabled,
  };
}

/** PUT body: the fields, plus the note when there is one. */
export function toUpdatePayload(form: AgentForm): AgentUpdate {
  const note = form.note.trim();
  return note ? { ...toAgentFields(form), note } : toAgentFields(form);
}

export function toCreatePayload(form: AgentForm): AgentCreate {
  return { key: form.key, ...toUpdatePayload(form) };
}

/** PUT body that flips `enabled` and keeps everything else as stored. */
export function toggledPayload(agent: Agent, enabled: boolean): AgentUpdate {
  return {
    name: agent.name,
    description: agent.description,
    instructions: agent.instructions,
    collections: agent.collections,
    examples: agent.examples,
    tools: agent.tools,
    model: agent.model,
    max_searches: agent.max_searches,
    max_tool_calls: agent.max_tool_calls,
    max_output_tokens: agent.max_output_tokens,
    enabled,
    note: enabled ? "Enabled" : "Disabled",
  };
}

/** The console's request: the unsaved form as a draft, in place of the saved agent if any. */
export function testRequest(form: AgentForm, question: string, savedKey: string | null): AgentTestRequest {
  const draft: AgentDraft = { ...toAgentFields(form) };
  if (!savedKey && isValidSlug(form.key)) draft.key = form.key;
  return { question: question.trim(), draft, ...(savedKey ? { key: savedKey } : {}) };
}

export const AGENT_SAVED_TOAST = "Takes effect on the next question; no deploy needed.";

// --- Versions ------------------------------------------------------------------------------

const FIELD_LABELS: Record<keyof AgentFields, string> = {
  name: "Name",
  description: "Description",
  instructions: "Instructions",
  collections: "Collections",
  examples: "Example questions",
  tools: "Tools",
  model: "Model",
  max_searches: "Searches per question",
  max_tool_calls: "Tool calls per question",
  max_output_tokens: "Output tokens per call",
  enabled: "Enabled",
};

export interface FieldChange {
  field: keyof AgentFields;
  label: string;
  before: string;
  after: string;
}

function shown(field: keyof AgentFields, value: unknown): string {
  if (value === undefined) return "(not recorded)";
  if (field === "model") return value ? String(value) : "Default for its role";
  if (field === "enabled") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "(none)";
  if (typeof value === "string") return value || "(empty)";
  return String(value);
}

/** What differs between a version and the current definition, field by field. */
export function diffSnapshots(version: AgentSnapshot, current: AgentSnapshot): FieldChange[] {
  const changes: FieldChange[] = [];
  for (const field of Object.keys(FIELD_LABELS) as (keyof AgentFields)[]) {
    const before = version[field];
    const after = current[field];
    if (JSON.stringify(before) === JSON.stringify(after)) continue;
    changes.push({ field, label: FIELD_LABELS[field], before: shown(field, before), after: shown(field, after) });
  }
  return changes;
}

export function snapshotOf(agent: Agent): AgentSnapshot {
  const { id: _id, version: _version, updated_at: _updated, ...snapshot } = agent;
  void _id;
  void _version;
  void _updated;
  return snapshot;
}

export function restoreText(agent: Pick<Agent, "name" | "version">, n: number): { title: string; description: string; confirm: string } {
  return {
    title: `Restore version ${n} of ${agent.name}?`,
    description: `This saves version ${agent.version + 1}, equal to version ${n}. Nothing in the history is removed, so you can restore version ${agent.version} again later. Changes not yet saved in the form are discarded. It takes effect on the next question.`,
    confirm: `Restore version ${n}`,
  };
}

// --- Test console --------------------------------------------------------------------------

const ROUTED_BY: Record<string, string> = {
  fast_path: "fast path (close match to an agent's examples, no planner call)",
  planner: "planner",
  fallback: "no agents enabled",
};

export function routedByLabel(routedBy: string): string {
  return ROUTED_BY[routedBy] ?? routedBy;
}

/** "Legal agent (draft)", "Legal agent and Finance agent", or "No agent: answered directly". */
export function routingSummary(routing: AgentTestRouting): string {
  if (routing.mode === "direct" || routing.agents.length === 0) return "No agent: the assistant answers directly";
  const names = routing.agents.map((a) => a.name);
  return names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** "5 calls · 3,120 in · 410 out · $0.004", or "cost not reported" when a price is unknown. */
export function formatTestUsage(usage: AgentTestUsage): string {
  const count = new Intl.NumberFormat("en");
  const cost =
    usage.cost_usd === null
      ? "cost not reported"
      : usage.cost_usd === 0
        ? "$0"
        : usage.cost_usd < 0.001
          ? "<$0.001"
          : `$${usage.cost_usd.toFixed(usage.cost_usd < 1 ? 3 : 2)}`;
  return `${usage.calls} ${usage.calls === 1 ? "call" : "calls"} · ${count.format(usage.input_tokens)} in · ${count.format(usage.output_tokens)} out · ${cost}`;
}

const STEP_ERRORS: Record<string, string> = {
  limit_reached: "over the tool-call limit, not run",
  not_offered: "not one of this agent's tools, not run",
  approval_required: "needs approval, not run",
  forbidden: "you lack the scope for it",
  invalid_input: "invalid input",
  tool_failed: "failed",
};

export function stepLabel(step: AgentTestStep): string {
  if (step.kind === "search") return `Searched “${step.detail}”`;
  if (step.ok) return `Called ${step.detail}`;
  return `Tried ${step.detail}: ${STEP_ERRORS[step.error ?? ""] ?? step.error ?? "failed"}`;
}
