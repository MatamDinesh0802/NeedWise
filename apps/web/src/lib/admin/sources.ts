/**
 * Admin → Sources (connectors). Shapes mirror apps/api/src/workmind/api/v1/sources.py; the
 * per-kind configuration mirrors modules/connectors/registry.py. Credentials are write-only:
 * the API only says whether they are set (`has_credentials`) and which fields it expects (`needs`).
 */

import { ApiError, apiJson, buildRequestInit, parseProblem, type ProblemDetails } from "@/lib/api";
import { slugError, suggestSlug } from "@/lib/slug";

export type SourceKind = "web" | "github" | "slack" | "gdrive";
export type SourceStatus = "idle" | "syncing" | "ok" | "error";

export interface SourceStats {
  added?: number;
  updated?: number;
  unchanged?: number;
  deleted?: number;
  failed?: number;
  items?: number;
  [key: string]: unknown;
}

export interface Source {
  id: string;
  key: string;
  name: string;
  kind: SourceKind;
  /** Collection key. */
  collection: string;
  config: Record<string, unknown>;
  has_credentials: boolean;
  needs: string[];
  interval_minutes: number;
  enabled: boolean;
  status: SourceStatus;
  last_sync_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  stats: SourceStats;
  documents: number;
}

export interface SourceCreate {
  key: string;
  name: string;
  kind: SourceKind;
  collection: string;
  config: Record<string, unknown>;
  credentials?: Record<string, string>;
  interval_minutes: number;
  enabled: boolean;
}

export interface SourceUpdate {
  name: string;
  config: Record<string, unknown>;
  interval_minutes: number;
  enabled: boolean;
}

export interface StaleDocument {
  id: string;
  title: string;
  collection: string;
  reason: "expired" | "not_updated";
  updated_at: string;
  valid_until: string | null;
}

const id = (value: string) => encodeURIComponent(value);

export const sourcesApi = {
  list: () => apiJson<Source[]>("/api/v1/admin/sources"),
  create: (body: SourceCreate) => apiJson<Source>("/api/v1/admin/sources", { method: "POST", json: body }),
  update: (key: string, body: SourceUpdate) => apiJson<Source>(`/api/v1/admin/sources/${id(key)}`, { method: "PUT", json: body }),
  /** Replaces the stored credentials; `{}` clears them. */
  setCredentials: (key: string, credentials: Record<string, string>) =>
    apiJson<void>(`/api/v1/admin/sources/${id(key)}/credentials`, { method: "PUT", json: credentials }),
  sync: (key: string) => apiJson<void>(`/api/v1/admin/sources/${id(key)}/sync`, { method: "POST" }),
  remove: (key: string) => apiJson<void>(`/api/v1/admin/sources/${id(key)}`, { method: "DELETE" }),
  stale: (days = 365) => apiJson<StaleDocument[]>(`/api/v1/admin/sources/stale?days=${days}`),
  googleStartUrl: (key: string) => `/api/v1/admin/sources/${id(key)}/oauth/google/start`,
  /**
   * Checks the Google start endpoint without following its redirect: a 409 (not configured,
   * wrong kind) becomes an ApiError to show in place; otherwise the browser should navigate.
   */
  checkGoogleStart: async (key: string, fetcher: typeof fetch = fetch): Promise<void> => {
    const response = await fetcher(sourcesApi.googleStartUrl(key), buildRequestInit({ redirect: "manual" }));
    if (response.type === "opaqueredirect" || response.ok) return; // on its way to Google
    let problem: ProblemDetails | null = null;
    try {
      problem = parseProblem(await response.json(), response.status);
    } catch {
      problem = null;
    }
    throw new ApiError({
      status: response.status,
      title: problem?.title ?? `HTTP ${response.status}`,
      code: problem?.code ?? null,
      detail: problem?.detail ?? null,
      problem,
    });
  },
};

export const sourcesKeys = {
  all: "admin/sources",
  list: "admin/sources/list",
  stale: (days: number) => `admin/sources/stale/${days}`,
};

// --- Labels -----------------------------------------------------------------------------------

export const KIND_LABELS: Record<SourceKind, string> = {
  web: "Website",
  github: "GitHub",
  slack: "Slack",
  gdrive: "Google Drive",
};

export const KIND_HINTS: Record<SourceKind, string> = {
  web: "Crawls pages from start URLs (respects robots.txt).",
  github: "Markdown and documents from a repository branch.",
  slack: "Channel history, one document per channel and month.",
  gdrive: "A folder and its subfolders; Docs, Sheets and Slides are exported.",
};

export const STATUS_LABELS: Record<SourceStatus, string> = {
  idle: "Not synced yet",
  syncing: "Syncing…",
  ok: "Synced",
  error: "Sync failed",
};

export const CREDENTIAL_LABELS: Record<string, { label: string; hint: string; optional?: boolean }> = {
  token: { label: "Access token", hint: "Only needed for private repositories (read-only scope is enough).", optional: true },
  bot_token: { label: "Bot token", hint: "xoxb-… with channels:history and channels:read." },
  refresh_token: { label: "Google account", hint: "Set by “Connect Google Drive”." },
};

/** "12 added · 3 updated · 40 unchanged" (zeros left out; "Nothing changed" if all zero). */
export function statsSummary(stats: SourceStats): string | null {
  const parts: string[] = [];
  for (const key of ["added", "updated", "unchanged", "deleted", "failed"] as const) {
    const value = stats[key];
    if (typeof value === "number" && value > 0) parts.push(`${value} ${key}`);
  }
  if (parts.length > 0) return parts.join(" · ");
  return typeof stats.items === "number" ? "Nothing changed" : null;
}

export const INTERVALS: readonly { value: number; label: string }[] = [
  { value: 15, label: "Every 15 minutes" },
  { value: 30, label: "Every 30 minutes" },
  { value: 60, label: "Every hour" },
  { value: 180, label: "Every 3 hours" },
  { value: 360, label: "Every 6 hours" },
  { value: 720, label: "Every 12 hours" },
  { value: 1440, label: "Every day" },
  { value: 10080, label: "Every week" },
];

export function intervalLabel(minutes: number): string {
  return INTERVALS.find((i) => i.value === minutes)?.label ?? `Every ${minutes} minutes`;
}

// --- The form ---------------------------------------------------------------------------------

export interface SourceForm {
  key: string;
  keyEdited: boolean;
  name: string;
  kind: SourceKind;
  collection: string;
  intervalMinutes: number;
  enabled: boolean;
  web: { startUrls: string; maxPages: string; pathPrefix: string };
  github: { repo: string; branch: string; paths: string };
  slack: { channels: string; days: string };
  gdrive: { folder: string };
  /** Write-only; sent on create (and with "Set credentials"). */
  credentials: Record<string, string>;
}

export const SOURCE_KEY_MAX = 40;

export function emptySourceForm(kind: SourceKind = "web"): SourceForm {
  return {
    key: "",
    keyEdited: false,
    name: "",
    kind,
    collection: "",
    intervalMinutes: 60,
    enabled: true,
    web: { startUrls: "", maxPages: "100", pathPrefix: "" },
    github: { repo: "", branch: "", paths: "" },
    slack: { channels: "", days: "90" },
    gdrive: { folder: "" },
    credentials: {},
  };
}

const lines = (text: string) =>
  text
    .split(/[\n,]+/)
    .map((l) => l.trim())
    .filter(Boolean);

const strings = (value: unknown): string[] => (Array.isArray(value) ? value.map(String) : []);

export function formFromSource(source: Source): SourceForm {
  const form = emptySourceForm(source.kind);
  const c = source.config;
  return {
    ...form,
    key: source.key,
    keyEdited: true,
    name: source.name,
    collection: source.collection,
    intervalMinutes: source.interval_minutes,
    enabled: source.enabled,
    web: {
      startUrls: strings(c.start_urls).join("\n"),
      maxPages: c.max_pages != null ? String(c.max_pages) : form.web.maxPages,
      pathPrefix: typeof c.path_prefix === "string" ? c.path_prefix : "",
    },
    github: {
      repo: typeof c.repo === "string" ? c.repo : "",
      branch: typeof c.branch === "string" ? c.branch : "",
      paths: strings(c.paths).filter(Boolean).join("\n"),
    },
    slack: { channels: strings(c.channels).join("\n"), days: c.days != null ? String(c.days) : form.slack.days },
    gdrive: { folder: typeof c.folder_id === "string" ? c.folder_id : "" },
  };
}

/** Name change, keeping the key in sync until it was edited by hand. */
export function withSourceName(form: SourceForm, name: string): SourceForm {
  return { ...form, name, key: form.keyEdited ? form.key : suggestSlug(name).slice(0, SOURCE_KEY_MAX).replace(/-+$/, "") };
}

/** A Drive folder link or a bare id → the id. */
export function driveFolderId(input: string): string {
  const value = input.trim();
  const match = /\/folders\/([\w-]+)/.exec(value) ?? /[?&]id=([\w-]+)/.exec(value);
  return match ? match[1] : value;
}

function intInRange(text: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(text.trim())) return null;
  const n = Number(text.trim());
  return n >= min && n <= max ? n : null;
}

export type SourceFormErrors = Partial<Record<string, string>>;

/** The `config` object for the chosen kind, plus errors keyed by field ("web.startUrls", …). */
export function configPayload(form: SourceForm): { config: Record<string, unknown>; errors: SourceFormErrors } {
  const errors: SourceFormErrors = {};
  switch (form.kind) {
    case "web": {
      const urls = lines(form.web.startUrls);
      const bad = urls.find((u) => !/^https?:\/\/[^\s/]+/.test(u));
      if (urls.length === 0) errors["web.startUrls"] = "Add at least one start URL.";
      else if (urls.length > 20) errors["web.startUrls"] = "At most 20 start URLs.";
      else if (bad) errors["web.startUrls"] = `“${bad}” isn't an http(s) URL.`;
      const maxPages = intInRange(form.web.maxPages, 1, 5000);
      if (maxPages === null) errors["web.maxPages"] = "A number from 1 to 5000.";
      const config: Record<string, unknown> = { start_urls: urls, max_pages: maxPages ?? 100 };
      if (form.web.pathPrefix.trim()) config.path_prefix = form.web.pathPrefix.trim();
      return { config, errors };
    }
    case "github": {
      const repo = form.github.repo.trim().replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "").replace(/\/+$/, "");
      if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) errors["github.repo"] = "Use owner/name, e.g. acme/handbook.";
      const paths = lines(form.github.paths);
      const config: Record<string, unknown> = { repo, paths: paths.length > 0 ? paths : [""] };
      if (form.github.branch.trim()) config.branch = form.github.branch.trim();
      return { config, errors };
    }
    case "slack": {
      const channels = lines(form.slack.channels);
      const bad = channels.find((c) => !/^[CG][A-Z0-9]{6,}$/.test(c));
      if (channels.length === 0) errors["slack.channels"] = "Add at least one channel id.";
      else if (channels.length > 100) errors["slack.channels"] = "At most 100 channels.";
      else if (bad) errors["slack.channels"] = `“${bad}” isn't a channel id (like C0123ABCD); copy it from the channel details.`;
      const days = intInRange(form.slack.days, 1, 3650);
      if (days === null) errors["slack.days"] = "A number of days from 1 to 3650.";
      return { config: { channels, days: days ?? 90 }, errors };
    }
    case "gdrive": {
      const folder = driveFolderId(form.gdrive.folder);
      if (folder.length < 5) errors["gdrive.folder"] = "Paste the folder's link or id.";
      return { config: { folder_id: folder }, errors };
    }
  }
}

export function validateSourceForm(form: SourceForm, { creating }: { creating: boolean }): SourceFormErrors {
  const errors: SourceFormErrors = { ...configPayload(form).errors };
  if (!form.name.trim()) errors.name = "Add a name.";
  if (creating) {
    const keyProblem = form.key.length > SOURCE_KEY_MAX ? `At most ${SOURCE_KEY_MAX} characters.` : slugError(form.key);
    if (keyProblem) errors.key = keyProblem;
    if (!form.collection) errors.collection = "Choose the collection documents go into.";
    if (form.kind === "slack" && !form.credentials.bot_token?.trim()) errors["credentials.bot_token"] = "Slack needs a bot token.";
  }
  if (form.intervalMinutes < 5 || form.intervalMinutes > 10080) errors.intervalMinutes = "From 5 minutes to a week.";
  return errors;
}

/** Credentials typed into the form (empty fields left out). */
export function credentialsPayload(credentials: Record<string, string>): Record<string, string> | undefined {
  const entries = Object.entries(credentials)
    .map(([k, v]) => [k, v.trim()] as const)
    .filter(([, v]) => v);
  return entries.length > 0 ? Object.fromEntries(entries) : undefined;
}

/** Credential fields the form asks for on create (Google Drive connects with OAuth instead). */
export function credentialFields(kind: SourceKind): string[] {
  if (kind === "github") return ["token"];
  if (kind === "slack") return ["bot_token"];
  return [];
}

export function toCreatePayload(form: SourceForm): SourceCreate {
  const body: SourceCreate = {
    key: form.key,
    name: form.name.trim(),
    kind: form.kind,
    collection: form.collection,
    config: configPayload(form).config,
    interval_minutes: form.intervalMinutes,
    enabled: form.enabled,
  };
  const credentials = form.kind === "gdrive" ? undefined : credentialsPayload(form.credentials);
  if (credentials) body.credentials = credentials;
  return body;
}

export function toUpdatePayload(form: SourceForm): SourceUpdate {
  return {
    name: form.name.trim(),
    config: configPayload(form).config,
    interval_minutes: form.intervalMinutes,
    enabled: form.enabled,
  };
}

/** Enable/disable from the list: the full update, with everything else as stored. */
export function toggledSource(source: Source, enabled: boolean): SourceUpdate {
  return { name: source.name, config: source.config, interval_minutes: source.interval_minutes, enabled };
}

/** Whether to keep refreshing the list (a sync is running). */
export function anySyncing(sources: readonly Pick<Source, "status">[]): boolean {
  return sources.some((s) => s.status === "syncing");
}

/** Server-side config errors (422 invalid_config) → form field keys. */
export function configFieldKey(kind: SourceKind, apiField: string): string {
  const map: Record<string, string> = {
    start_urls: "web.startUrls",
    max_pages: "web.maxPages",
    path_prefix: "web.pathPrefix",
    repo: "github.repo",
    branch: "github.branch",
    paths: "github.paths",
    channels: "slack.channels",
    days: "slack.days",
    folder_id: "gdrive.folder",
  };
  const key = map[apiField];
  return key && key.startsWith(`${kind}.`) ? key : "config";
}
