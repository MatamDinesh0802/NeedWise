/**
 * Department agents (/api/v1/admin/agents): types, API, and the form ↔ payload mapping.
 * Shapes mirror apps/api/src/workmind/api/v1/agents.py. Pure parts are unit-tested.
 */

import { apiJson } from "@/lib/api";
import { isValidSlug, suggestSlug } from "@/lib/slug";

export interface Agent {
  id: string;
  key: string;
  name: string;
  description: string;
  instructions: string;
  collections: string[];
  examples: string[];
  max_searches: number;
  enabled: boolean;
  updated_at: string;
}

export type AgentFields = Omit<Agent, "id" | "key" | "updated_at">;
export type AgentCreate = AgentFields & { key: string };

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
} as const;

const BASE = "/api/v1/admin/agents";

export const agentsApi = {
  list: () => apiJson<{ items: Agent[] }>(BASE),
  create: (body: AgentCreate) => apiJson<Agent>(BASE, { method: "POST", json: body }),
  update: (key: string, body: AgentFields) =>
    apiJson<Agent>(`${BASE}/${encodeURIComponent(key)}`, { method: "PUT", json: body }),
};

export const agentsKey = "admin/agents";

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
  maxSearches: number;
  enabled: boolean;
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
    maxSearches: 2,
    enabled: true,
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
    maxSearches: agent.max_searches,
    enabled: agent.enabled,
  };
}

/** Keys are slugs, at most 40 characters: "Customer Support" → "customer-support". */
export function suggestAgentKey(name: string): string {
  return suggestSlug(name).slice(0, AGENT_LIMITS.keyMax).replace(/-+$/, "");
}

/** Name change, keeping the key in sync until it was edited by hand. */
export function withName(form: AgentForm, name: string): AgentForm {
  return { ...form, name, key: form.keyEdited ? form.key : suggestAgentKey(name) };
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

export type AgentFormErrors = Partial<Record<"key" | "name" | "description" | "instructions" | "collections" | "examples" | "maxSearches", string>>;

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
  if (!Number.isInteger(form.maxSearches) || form.maxSearches < AGENT_LIMITS.searchesMin || form.maxSearches > AGENT_LIMITS.searchesMax) {
    errors.maxSearches = `Choose between ${AGENT_LIMITS.searchesMin} and ${AGENT_LIMITS.searchesMax} searches.`;
  }
  return errors;
}

export function toAgentFields(form: AgentForm): AgentFields {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    instructions: form.instructions.trim(),
    collections: [...new Set(form.collections)],
    examples: cleanExamples(form.examples),
    max_searches: form.maxSearches,
    enabled: form.enabled,
  };
}

export function toCreatePayload(form: AgentForm): AgentCreate {
  return { key: form.key, ...toAgentFields(form) };
}

/** PUT body that flips `enabled` and keeps everything else as stored. */
export function toggledPayload(agent: Agent, enabled: boolean): AgentFields {
  return {
    name: agent.name,
    description: agent.description,
    instructions: agent.instructions,
    collections: agent.collections,
    examples: agent.examples,
    max_searches: agent.max_searches,
    enabled,
  };
}

export const AGENT_SAVED_TOAST = "Takes effect on the next question; no deploy needed.";
