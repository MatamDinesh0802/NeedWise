/**
 * Types and pure view logic for GET /api/v1/system/status.
 * Kept free of React so it can be unit-tested in isolation.
 */

export const SYSTEM_STATUS_ENDPOINT = "/api/v1/system/status";
export const SYSTEM_STATUS_REFRESH_MS = 10_000;

/** Canonical display order; unknown components are appended alphabetically. */
export const KNOWN_COMPONENTS = ["postgres", "valkey", "qdrant", "openfga", "keycloak"] as const;

const COMPONENT_LABELS: Record<string, string> = {
  postgres: "PostgreSQL",
  valkey: "Valkey",
  qdrant: "Qdrant",
  openfga: "OpenFGA",
  keycloak: "Keycloak",
};

export type ComponentHealth = "ok" | "down";

export interface ComponentStatus {
  name: string;
  status: ComponentHealth;
  latency_ms: number | null;
  detail: string | null;
}

export interface SystemStatus {
  status: "ok" | "degraded";
  product: string;
  version: string;
  environment: string;
  components: ComponentStatus[];
}

export type StatusFetchState =
  | { kind: "loading" }
  | { kind: "success"; data: SystemStatus; checkedAt: Date }
  | { kind: "error"; message: string; checkedAt: Date };

export type StatusTone = "ok" | "warning" | "error" | "neutral";

export interface ComponentView {
  name: string;
  label: string;
  healthy: boolean;
  latency: string;
  detail: string | null;
}

export interface StatusView {
  tone: StatusTone;
  headline: string;
  summary: string;
  version: string | null;
  environment: string | null;
  components: ComponentView[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Validates an unknown JSON payload against the status contract. Throws on mismatch. */
export function parseSystemStatus(payload: unknown): SystemStatus {
  if (!isRecord(payload)) throw new Error("Status payload is not an object");
  const { status, product, version, environment, components } = payload;
  if (status !== "ok" && status !== "degraded") throw new Error("Invalid overall status");
  if (typeof product !== "string" || typeof version !== "string" || typeof environment !== "string") {
    throw new Error("Missing product metadata");
  }
  if (!Array.isArray(components)) throw new Error("Components must be an array");

  return {
    status,
    product,
    version,
    environment,
    components: components.map((raw, index): ComponentStatus => {
      if (!isRecord(raw) || typeof raw.name !== "string") {
        throw new Error(`Component #${index} is malformed`);
      }
      if (raw.status !== "ok" && raw.status !== "down") {
        throw new Error(`Component "${raw.name}" has an invalid status`);
      }
      return {
        name: raw.name,
        status: raw.status,
        latency_ms: typeof raw.latency_ms === "number" ? raw.latency_ms : null,
        detail: typeof raw.detail === "string" ? raw.detail : null,
      };
    }),
  };
}

export function componentLabel(name: string): string {
  return COMPONENT_LABELS[name] ?? name;
}

export function formatLatency(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function componentOrder(name: string): number {
  const index = (KNOWN_COMPONENTS as readonly string[]).indexOf(name);
  return index === -1 ? KNOWN_COMPONENTS.length : index;
}

export function sortComponents(components: ComponentStatus[]): ComponentStatus[] {
  return [...components].sort(
    (a, b) => componentOrder(a.name) - componentOrder(b.name) || a.name.localeCompare(b.name),
  );
}

/** Maps the fetch state to everything the status card needs to render. */
export function toStatusView(state: StatusFetchState): StatusView {
  if (state.kind === "loading") {
    return {
      tone: "neutral",
      headline: "Checking system status…",
      summary: "Contacting the API.",
      version: null,
      environment: null,
      components: [],
    };
  }

  if (state.kind === "error") {
    return {
      tone: "error",
      headline: "API unreachable",
      summary: `The web app could not reach the API (${state.message}). Is the backend running?`,
      version: null,
      environment: null,
      components: [],
    };
  }

  const { data } = state;
  const components = sortComponents(data.components).map(
    (c): ComponentView => ({
      name: c.name,
      label: componentLabel(c.name),
      healthy: c.status === "ok",
      latency: formatLatency(c.latency_ms),
      detail: c.detail,
    }),
  );
  const down = components.filter((c) => !c.healthy).length;
  const allHealthy = data.status === "ok" && down === 0;

  return {
    tone: allHealthy ? "ok" : "warning",
    headline: allHealthy ? "All systems operational" : "Degraded",
    summary: allHealthy
      ? `${components.length} of ${components.length} components healthy.`
      : `${down} of ${components.length} components down.`,
    version: data.version,
    environment: data.environment,
    components,
  };
}
