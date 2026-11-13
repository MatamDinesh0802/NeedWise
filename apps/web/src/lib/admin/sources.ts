/**
 * Sources (connectors) and their health: Admin → Sources for org admins, Knowledge Hub → Sources
 * for the owners of the collections they sync into. Shapes mirror
 * apps/api/src/workmind/api/v1/sources.py; the per-kind configuration mirrors
 * modules/connectors/registry.py. Credentials are write-only: the API only says whether they are
 * set (`has_credentials`) and which fields it expects (`needs`).
 */

import { ApiError, apiJson, buildRequestInit, parseProblem, type ProblemDetails } from "@/lib/api";
import { slugError, suggestSlug } from "@/lib/slug";

export type SourceKind = "web" | "github" | "slack" | "gdrive";
export type SourceStatus = "idle" | "syncing" | "ok" | "error";
export type SourceHealth = "healthy" | "failing" | "paused" | "never_synced";
export type OAuthProvider = "google" | "slack";
/** Which page a source lives on: every source (org admins) or the collections you own. */
export type SourcesScope = "all" | "owned";

export interface SourceStats {
  added?: number;
  updated?: number;
  unchanged?: number;
  deleted?: number;
  failed?: number;
  missing?: number;
  items?: number;
  [key: string]: unknown;
}

export interface PersonRef {
  id: string;
  display_name: string;
}

/** Who can see what a source syncs: its collection's access, as names. */
export interface AccessEntry {
  principal: string;
  relation: "owner" | "editor" | "viewer";
  label: string;
}

export interface RunError {
  /** The item's id in the source (a URL, a path, a file id), or null for the whole sync. */
  item: string | null;
  /** What the source or the sync engine said. */
  message: string;
  /** What it means and what to do. */
  plain: string;
}

export interface SourceRun {
  id: string;
  trigger: "schedule" | "manual";
  requested_by: PersonRef | null;
  status: "running" | "ok" | "error";
  started_at: string;
  finished_at: string | null;
  /** Whether the scan listed everything (only then are deletions applied). */
  complete: boolean | null;
  stats: SourceStats;
  errors: RunError[];
}

export interface Source {
  id: string;
  key: string;
  name: string;
  kind: SourceKind;
  /** Collection key. */
  collection: string;
  collection_id: string;
  collection_name: string;
  collection_scope: string;
  access: AccessEntry[];
  owner: PersonRef | null;
  config: Record<string, unknown>;
  has_credentials: boolean;
  needs: string[];
  oauth: OAuthProvider | null;
  interval_minutes: number;
  enabled: boolean;
  status: SourceStatus;
  health: SourceHealth;
  syncing: boolean;
  consecutive_failures: number;
  failure_alerted_at: string | null;
  last_sync_at: string | null;
  last_success_at: string | null;
  next_sync_at: string | null;
  last_error: string | null;
  last_error_plain: string | null;
  last_run: SourceRun | null;
  stats: SourceStats;
  documents: number;
  stale_after_days: number | null;
  stale: { not_updated: number; missing_upstream: number };
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
  stale_after_days: number | null;
}

export interface SourceUpdate {
  name: string;
  config: Record<string, unknown>;
  interval_minutes: number;
  enabled: boolean;
  stale_after_days: number | null;
}

export interface KindOption {
  kind: SourceKind;
  credentials: string[];
  oauth: OAuthProvider | null;
  /** The deployment has this provider's OAuth client (GOOGLE_CLIENT_*, SLACK_CLIENT_*). */
  oauth_configured: boolean;
  default_stale_after_days: number | null;
}

export interface CollectionOption {
  id: string;
  key: string;
  name: string;
  scope: string;
}

export interface SourceOptions {
  scope: SourcesScope;
  kinds: KindOption[];
  /** Where the caller may add a source: every collection (admins) or the ones they own. */
  collections: CollectionOption[];
  failure_alert_after: number;
}

export type StaleReason = "not_updated" | "missing_upstream";

export interface StaleSyncedDocument {
  id: string;
  /** Null when you can't open the collection (an admin of a restricted one): no title, no link. */
  title: string | null;
  url: string | null;
  collection_id: string;
  collection_name: string;
  source: string;
  source_name: string;
  reason: StaleReason;
  changed_at: string | null;
  missing_since: string | null;
  owner: PersonRef | null;
  reminded_at: string | null;
}

/** Documents of every collection (uploads too) past valid_until or not updated: org admins only. */
export interface StaleDocument {
  id: string;
  /** Null for a document the admin can't read (its collection, or its own access list). */
  title: string | null;
  collection: string;
  reason: "expired" | "not_updated";
  updated_at: string;
  valid_until: string | null;
}

/** Where the browser comes back after the provider's consent screen. */
export type OAuthBack = "admin" | "knowledge";

const id = (value: string) => encodeURIComponent(value);
const BASE = "/api/v1/admin/sources";

function staleQuery(filter: { source?: string | null; days?: number | null }): string {
  const params = new URLSearchParams();
  if (filter.source) params.set("source", filter.source);
  if (filter.days) params.set("days", String(filter.days));
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const sourcesApi = {
  list: () => apiJson<Source[]>(BASE),
  options: () => apiJson<SourceOptions>(`${BASE}/options`),
  create: (body: SourceCreate) => apiJson<Source>(BASE, { method: "POST", json: body }),
  update: (key: string, body: SourceUpdate) => apiJson<Source>(`${BASE}/${id(key)}`, { method: "PUT", json: body }),
  pause: (key: string) => apiJson<Source>(`${BASE}/${id(key)}/pause`, { method: "POST" }),
  resume: (key: string) => apiJson<Source>(`${BASE}/${id(key)}/resume`, { method: "POST" }),
  /** Replaces the stored credentials; `{}` clears them. */
  setCredentials: (key: string, credentials: Record<string, string>) =>
    apiJson<void>(`${BASE}/${id(key)}/credentials`, { method: "PUT", json: credentials }),
  sync: (key: string) => apiJson<void>(`${BASE}/${id(key)}/sync`, { method: "POST" }),
  /** Disconnect: the source, its credentials, its history and every document it synced. */
  remove: (key: string) => apiJson<void>(`${BASE}/${id(key)}`, { method: "DELETE" }),
  runs: (key: string, limit = 20) => apiJson<SourceRun[]>(`${BASE}/${id(key)}/runs?limit=${limit}`),
  staleDocuments: (filter: { source?: string | null; days?: number | null } = {}) =>
    apiJson<StaleSyncedDocument[]>(`${BASE}/stale-documents${staleQuery(filter)}`),
  stale: (days = 365) => apiJson<StaleDocument[]>(`${BASE}/stale?days=${days}`),
  oauthStartUrl: (provider: OAuthProvider, key: string, back: OAuthBack = "admin") =>
    `${BASE}/${id(key)}/oauth/${provider}/start${back === "admin" ? "" : `?back=${back}`}`,
  googleStartUrl: (key: string) => sourcesApi.oauthStartUrl("google", key),
  /**
   * Checks the OAuth start endpoint without following its redirect: a 409 (not configured,
   * wrong kind) becomes an ApiError to show in place; otherwise the browser should navigate.
   */
  checkOAuthStart: async (provider: OAuthProvider, key: string, back: OAuthBack = "admin", fetcher: typeof fetch = fetch): Promise<void> => {
    const response = await fetcher(sourcesApi.oauthStartUrl(provider, key, back), buildRequestInit({ redirect: "manual" }));
    if (response.type === "opaqueredirect" || response.ok) return; // on its way to the provider
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
  checkGoogleStart: (key: string, fetcher: typeof fetch = fetch) => sourcesApi.checkOAuthStart("google", key, "admin", fetcher),
};

export const sourcesKeys = {
  all: "admin/sources",
  list: "admin/sources/list",
  options: "admin/sources/options",
  runs: (key: string) => `admin/sources/runs/${key}`,
  staleDocuments: (source: string | null, days: number | null) => `admin/sources/stale-documents/${source ?? "*"}/${days ?? "own"}`,
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

export const OAUTH_LABELS: Record<OAuthProvider, string> = { google: "Google Drive", slack: "Slack" };

export const STATUS_LABELS: Record<SourceStatus, string> = {
  idle: "Not synced yet",
  syncing: "Syncing…",
  ok: "Synced",
  error: "Sync failed",
};

export const HEALTH_LABELS: Record<SourceHealth, string> = {
  healthy: "Healthy",
  failing: "Failing",
  paused: "Paused",
  never_synced: "Never synced",
};

export const HEALTH_HINTS: Record<SourceHealth, string> = {
  healthy: "The last sync succeeded.",
  failing: "The last sync failed; it is retried at the next interval.",
  paused: "No scheduled syncs until it is resumed. Its documents stay searchable.",
  never_synced: "No sync has succeeded yet.",
};

export const HEALTH_ORDER: readonly SourceHealth[] = ["failing", "never_synced", "paused", "healthy"];

export const CREDENTIAL_LABELS: Record<string, { label: string; hint: string; optional?: boolean }> = {
  token: { label: "Access token", hint: "Only needed for private repositories (read-only scope is enough).", optional: true },
  bot_token: { label: "Bot token", hint: "xoxb-… with channels:history and channels:read (groups:* for private channels)." },
  refresh_token: { label: "Google account", hint: "Set by “Connect Google Drive”." },
};

export const RELATION_LABELS: Record<AccessEntry["relation"], string> = { owner: "owner", editor: "can edit", viewer: "can view" };

/** "12 added · 3 updated · 40 unchanged" (zeros left out; "Nothing changed" if all zero). */
export function statsSummary(stats: SourceStats): string | null {
  const parts: string[] = [];
  for (const key of ["added", "updated", "unchanged", "deleted", "failed"] as const) {
    const value = stats[key];
    if (typeof value === "number" && value > 0) parts.push(`${value} ${key}`);
  }
  if (typeof stats.missing === "number" && stats.missing > 0) parts.push(`${stats.missing} missing upstream`);
  // Gone from the source, kept because a legal hold covers them (docs/enterprise.md#legal-hold).
  const kept = stats.kept_under_hold;
  if (typeof kept === "number" && kept > 0) parts.push(`${kept} kept under legal hold`);
  if (parts.length > 0) return parts.join(" · ");
  return typeof stats.items === "number" ? "Nothing changed" : null;
}

/** What a run changed: added, updated and deleted always shown (the dashboard's columns). */
export function changeCounts(stats: SourceStats): { added: number; updated: number; deleted: number } {
  const n = (value: unknown) => (typeof value === "number" ? value : 0);
  return { added: n(stats.added), updated: n(stats.updated), deleted: n(stats.deleted) };
}

/** "Sync now" by Erin, or "Scheduled". */
export function triggerLabel(run: Pick<SourceRun, "trigger" | "requested_by">): string {
  if (run.trigger === "manual") return run.requested_by ? `Sync now by ${run.requested_by.display_name}` : "Sync now";
  return "Scheduled";
}

/** "8 s", "2 min 5 s", or null while running. */
export function runDuration(run: Pick<SourceRun, "started_at" | "finished_at">): string | null {
  if (!run.finished_at) return null;
  const seconds = Math.max(0, Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000));
  if (!Number.isFinite(seconds)) return null;
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
}

/** When the scheduler picks the source up next, in words. */
export function nextSyncText(source: Pick<Source, "enabled" | "syncing" | "next_sync_at">, now: Date = new Date()): string {
  if (!source.enabled) return "Paused";
  if (source.syncing) return "Syncing now";
  if (!source.next_sync_at) return "—";
  const at = new Date(source.next_sync_at);
  if (Number.isNaN(at.getTime())) return "—";
  if (at.getTime() <= now.getTime() + 60_000) return "Due now (within a minute)";
  return relativeFuture(at, now);
}

function relativeFuture(at: Date, now: Date): string {
  const minutes = Math.round((at.getTime() - now.getTime()) / 60_000);
  if (minutes < 60) return `In ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `In ${hours} ${hours === 1 ? "hour" : "hours"}`;
  const days = Math.round(hours / 24);
  return `In ${days} days`;
}

/** "Failed 3 times in a row · its managers were notified". */
export function failureText(source: Pick<Source, "consecutive_failures" | "failure_alerted_at">, threshold: number | null): string | null {
  const n = source.consecutive_failures;
  if (n <= 0) return null;
  const times = n === 1 ? "Failed once" : `Failed ${n} times in a row`;
  if (source.failure_alerted_at) return `${times} · the people who manage it were notified`;
  if (threshold && n < threshold) return `${times} · they're notified after ${threshold} in a row`;
  return times;
}

/** "Alice Moreau (owner) · Finance (department) can view · …". */
export function accessSummary(access: readonly AccessEntry[]): string {
  if (access.length === 0) return "Nobody but org admins";
  return access.map((a) => (a.relation === "owner" ? `${a.label} (owner)` : `${a.label} ${RELATION_LABELS[a.relation]}`)).join(" · ");
}

/** What the source reads, in one line ("3 start URLs", "acme/handbook (main)", "2 channels", "Drive folder 1AbC…"). */
export function whatSyncs(source: Pick<Source, "kind" | "config">): string {
  const c = source.config;
  const list = (value: unknown) => (Array.isArray(value) ? value.map(String) : []);
  switch (source.kind) {
    case "web": {
      const urls = list(c.start_urls);
      return urls.length === 1 ? urls[0] : `${urls.length} start URLs`;
    }
    case "github": {
      const repo = typeof c.repo === "string" ? c.repo : "a repository";
      return typeof c.branch === "string" && c.branch ? `${repo} (${c.branch})` : repo;
    }
    case "slack": {
      const channels = list(c.channels);
      return channels.length === 1 ? `Slack channel ${channels[0]}` : `${channels.length} Slack channels`;
    }
    case "gdrive":
      return typeof c.folder_id === "string" ? `Drive folder ${c.folder_id}` : "A Drive folder";
  }
}

/** How each health counts across sources (for the summary bar). */
export function healthCounts(sources: readonly Pick<Source, "health">[]): Record<SourceHealth, number> {
  const counts: Record<SourceHealth, number> = { healthy: 0, failing: 0, paused: 0, never_synced: 0 };
  for (const s of sources) counts[s.health] += 1;
  return counts;
}

/** Failing first, then never synced, paused, healthy; by name within each. */
export function sortSources<T extends Pick<Source, "health" | "name">>(sources: readonly T[]): T[] {
  return [...sources].sort((a, b) => HEALTH_ORDER.indexOf(a.health) - HEALTH_ORDER.indexOf(b.health) || a.name.localeCompare(b.name));
}

/** The stale-document reason in words. */
export function staleReasonText(doc: Pick<StaleSyncedDocument, "reason" | "changed_at" | "missing_since">, format: (iso: string) => string): string {
  if (doc.reason === "missing_upstream") {
    return doc.missing_since ? `Not listed by the source since ${format(doc.missing_since)}` : "Not listed by the source any more";
  }
  return doc.changed_at ? `Unchanged in the source since ${format(doc.changed_at)}` : "Unchanged in the source for a long time";
}

/** Typed confirmation for Disconnect: the source's key, exactly. */
export function disconnectConfirmed(typed: string, source: Pick<Source, "key">): boolean {
  return typed.trim() === source.key;
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

/** "Stale after": the select's options ("never": no threshold). */
export const STALE_AFTER_OPTIONS: readonly { value: string; label: string }[] = [
  { value: "never", label: "Never (an archive, e.g. chat history)" },
  { value: "30", label: "Unchanged for 30 days" },
  { value: "90", label: "Unchanged for 3 months" },
  { value: "180", label: "Unchanged for 6 months" },
  { value: "365", label: "Unchanged for a year" },
  { value: "730", label: "Unchanged for 2 years" },
];

export function staleAfterLabel(days: number | null): string {
  if (days === null) return "Never";
  return STALE_AFTER_OPTIONS.find((o) => o.value === String(days))?.label ?? `Unchanged for ${days} days`;
}

export function staleAfterValue(days: number | null): string {
  return days === null ? "never" : String(days);
}

export function staleAfterFromValue(value: string): number | null {
  const days = Number(value);
  return value === "never" || !Number.isInteger(days) || days < 1 ? null : days;
}

/** The options, plus the stored threshold when it isn't one of them (set through the API). */
export function staleAfterOptions(days: number | null): { value: string; label: string }[] {
  const listed = STALE_AFTER_OPTIONS.some((o) => o.value === staleAfterValue(days));
  return listed ? [...STALE_AFTER_OPTIONS] : [...STALE_AFTER_OPTIONS, { value: staleAfterValue(days), label: staleAfterLabel(days) }];
}

/** The kind's default threshold when the API's options aren't loaded (mirrors default_stale_after). */
export function defaultStaleAfter(kind: SourceKind, options?: SourceOptions | null): number | null {
  const fromApi = options?.kinds.find((k) => k.kind === kind);
  if (fromApi) return fromApi.default_stale_after_days;
  return kind === "slack" ? null : 180;
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
  staleAfterDays: number | null;
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
    staleAfterDays: defaultStaleAfter(kind),
    web: { startUrls: "", maxPages: "100", pathPrefix: "" },
    github: { repo: "", branch: "", paths: "" },
    slack: { channels: "", days: "90" },
    gdrive: { folder: "" },
    credentials: {},
  };
}

/** Another kind chosen in the form: its credentials and staleness default start over. */
export function withKind(form: SourceForm, kind: SourceKind, options?: SourceOptions | null): SourceForm {
  return { ...form, kind, credentials: {}, staleAfterDays: defaultStaleAfter(kind, options) };
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
    staleAfterDays: source.stale_after_days,
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

/**
 * `slackOAuth`: the deployment can "Connect Slack", so a new Slack source may be saved without a
 * pasted bot token (it connects right after).
 */
export function validateSourceForm(form: SourceForm, { creating, slackOAuth = false }: { creating: boolean; slackOAuth?: boolean }): SourceFormErrors {
  const errors: SourceFormErrors = { ...configPayload(form).errors };
  if (!form.name.trim()) errors.name = "Add a name.";
  if (creating) {
    const keyProblem = form.key.length > SOURCE_KEY_MAX ? `At most ${SOURCE_KEY_MAX} characters.` : slugError(form.key);
    if (keyProblem) errors.key = keyProblem;
    if (!form.collection) errors.collection = "Choose the collection documents go into.";
    if (form.kind === "slack" && !slackOAuth && !form.credentials.bot_token?.trim()) errors["credentials.bot_token"] = "Slack needs a bot token.";
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
    stale_after_days: form.staleAfterDays,
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
    stale_after_days: form.staleAfterDays,
  };
}

/** Whether to keep refreshing the list (a sync is running). */
export function anySyncing(sources: readonly Pick<Source, "syncing">[]): boolean {
  return sources.some((s) => s.syncing);
}

/**
 * What to do after creating a source: connect it with OAuth (Drive always, Slack when no bot
 * token was typed and the deployment can connect Slack), or sync it right away.
 */
export function afterCreate(source: Pick<Source, "kind" | "enabled" | "has_credentials" | "oauth">): "connect" | "sync" | "nothing" {
  if (source.oauth && !source.has_credentials) return "connect";
  return source.enabled ? "sync" : "nothing";
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

/** After a provider's consent screen the API redirects to …/sources?oauth=connected|failed&provider=…&source=…. */
export function oauthResult(params: Pick<URLSearchParams, "get">): { ok: boolean; provider: OAuthProvider; source: string | null } | null {
  const result = params.get("oauth");
  if (result !== "connected" && result !== "failed") return null;
  const provider: OAuthProvider = params.get("provider") === "slack" ? "slack" : "google";
  return { ok: result === "connected", provider, source: params.get("source") };
}
