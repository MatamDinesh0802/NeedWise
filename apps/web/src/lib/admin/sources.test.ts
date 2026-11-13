import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { runFixture, sourceFixture, staleFixture } from "@/test/sources-fixtures";

import {
  accessSummary,
  afterCreate,
  changeCounts,
  configFieldKey,
  configPayload,
  disconnectConfirmed,
  driveFolderId,
  emptySourceForm,
  failureText,
  formFromSource,
  healthCounts,
  nextSyncText,
  oauthResult,
  runDuration,
  sortSources,
  sourcesApi,
  staleAfterFromValue,
  staleAfterLabel,
  staleAfterOptions,
  staleAfterValue,
  staleReasonText,
  statsSummary,
  toCreatePayload,
  toUpdatePayload,
  triggerLabel,
  validateSourceForm,
  whatSyncs,
  withKind,
  withSourceName,
  type Source,
  type SourceForm,
} from "./sources";

const form = (kind: SourceForm["kind"], patch: Partial<SourceForm> = {}): SourceForm => ({
  ...withSourceName(emptySourceForm(kind), "Handbook site"),
  collection: "company-handbook",
  ...patch,
});

describe("per-kind config forms → payload", () => {
  it("web: start URLs one per line, max pages, optional path prefix", () => {
    const f = form("web", { web: { startUrls: "http://host.docker.internal:8090/handbook/\n\nhttps://intranet.example/hr/", maxPages: "50", pathPrefix: "" } });
    expect(toCreatePayload(f)).toEqual({
      key: "handbook-site",
      name: "Handbook site",
      kind: "web",
      collection: "company-handbook",
      config: { start_urls: ["http://host.docker.internal:8090/handbook/", "https://intranet.example/hr/"], max_pages: 50 },
      interval_minutes: 60,
      enabled: true,
      stale_after_days: 180,
    });
    expect(configPayload({ ...f, web: { ...f.web, pathPrefix: " /handbook/ " } }).config).toMatchObject({ path_prefix: "/handbook/" });
  });

  it("web: checks URLs and the page limit", () => {
    expect(configPayload(form("web")).errors).toEqual({ "web.startUrls": "Add at least one start URL." });
    expect(configPayload(form("web", { web: { startUrls: "intranet/handbook", maxPages: "0", pathPrefix: "" } })).errors).toEqual({
      "web.startUrls": "“intranet/handbook” isn't an http(s) URL.",
      "web.maxPages": "A number from 1 to 5000.",
    });
  });

  it("github: owner/name (a pasted URL works too), optional branch, paths default to the whole repo", () => {
    const f = form("github", { github: { repo: "https://github.com/acme/handbook.git", branch: "", paths: "" }, credentials: { token: " ghp_x " } });
    expect(toCreatePayload(f)).toMatchObject({ config: { repo: "acme/handbook", paths: [""] }, credentials: { token: "ghp_x" } });
    expect(configPayload({ ...f, github: { repo: "acme/handbook", branch: "main", paths: "docs\npolicies" } }).config).toEqual({
      repo: "acme/handbook",
      branch: "main",
      paths: ["docs", "policies"],
    });
    expect(configPayload(form("github", { github: { repo: "handbook", branch: "", paths: "" } })).errors).toEqual({
      "github.repo": "Use owner/name, e.g. acme/handbook.",
    });
  });

  it("github: an empty token isn't sent (public repositories)", () => {
    expect(toCreatePayload(form("github", { github: { repo: "acme/handbook", branch: "", paths: "" }, credentials: { token: "  " } }))).not.toHaveProperty(
      "credentials",
    );
  });

  it("slack: channel ids and days of history; a bot token is required", () => {
    const f = form("slack", { slack: { channels: "C0123ABCD, C0456EFGH", days: "30" }, credentials: { bot_token: "xoxb-1" } });
    expect(toCreatePayload(f)).toMatchObject({ config: { channels: ["C0123ABCD", "C0456EFGH"], days: 30 }, credentials: { bot_token: "xoxb-1" } });
    expect(validateSourceForm({ ...f, credentials: {} }, { creating: true })).toEqual({ "credentials.bot_token": "Slack needs a bot token." });
    expect(configPayload(form("slack", { slack: { channels: "#general", days: "90" } })).errors["slack.channels"]).toMatch(/isn't a channel id/);
  });

  it("gdrive: a folder link or id; credentials come from OAuth, never the form", () => {
    expect(driveFolderId("https://drive.google.com/drive/folders/1AbCdEfGhIjK?usp=sharing")).toBe("1AbCdEfGhIjK");
    expect(driveFolderId("https://drive.google.com/open?id=1XyZ12345")).toBe("1XyZ12345");
    expect(driveFolderId(" 1AbCdEfGhIjK ")).toBe("1AbCdEfGhIjK");
    const f = form("gdrive", { gdrive: { folder: "https://drive.google.com/drive/folders/1AbCdEfGhIjK" }, credentials: { token: "x" } });
    expect(toCreatePayload(f)).toEqual(expect.objectContaining({ config: { folder_id: "1AbCdEfGhIjK" } }));
    expect(toCreatePayload(f)).not.toHaveProperty("credentials");
  });

  it("confluence: the site, space keys and attachments; an API token, or OAuth when it is set up", () => {
    const f = form("confluence", {
      confluence: { siteUrl: "https://acme.atlassian.net/wiki", spaces: "ENG\nHR", includeAttachments: false },
      credentials: { email: "sync@acme.test", api_token: "ATATT-1" },
    });
    expect(toCreatePayload(f)).toMatchObject({
      config: { site_url: "https://acme.atlassian.net/wiki", spaces: ["ENG", "HR"], include_attachments: false },
      credentials: { email: "sync@acme.test", api_token: "ATATT-1" },
    });
    expect(validateSourceForm(f, { creating: true })).toEqual({});
    const bare = { ...f, credentials: {} };
    expect(validateSourceForm(bare, { creating: true })["credentials.email"]).toMatch(/API token/);
    expect(validateSourceForm(bare, { creating: true, atlassianOAuth: true })).toEqual({});
    expect(validateSourceForm({ ...f, credentials: { email: "sync@acme.test" } }, { creating: true })).toEqual({ "credentials.api_token": "Add the API token for this account." });
    expect(configPayload(form("confluence", { confluence: { siteUrl: "https://wiki.internal", spaces: "ENG", includeAttachments: true } })).errors["confluence.siteUrl"]).toMatch(/atlassian\.net/);
    expect(configPayload(form("confluence", { confluence: { siteUrl: "https://acme.atlassian.net", spaces: "", includeAttachments: true } })).errors["confluence.spaces"]).toBe("Add at least one space key.");
  });

  it("sharepoint: an optional site, library and folder; it always connects with Microsoft", () => {
    const f = form("sharepoint", {
      sharepoint: { siteUrl: "https://contoso.sharepoint.com/sites/finance", library: "Policies", folder: "/2026/" },
      credentials: { token: "x" },
    });
    expect(toCreatePayload(f)).toMatchObject({ config: { site_url: "https://contoso.sharepoint.com/sites/finance", library: "Policies", folder: "2026" } });
    expect(toCreatePayload(f)).not.toHaveProperty("credentials");
    expect(configPayload(form("sharepoint")).config).toEqual({}); // the connecting account's OneDrive
    expect(configPayload(form("sharepoint", { sharepoint: { siteUrl: "https://contoso.example.com", library: "", folder: "" } })).errors["sharepoint.siteUrl"]).toMatch(/SharePoint site/);
  });

  it("validates key, name and collection on create only", () => {
    const f = { ...emptySourceForm("web"), web: { startUrls: "https://x.example/", maxPages: "10", pathPrefix: "" } };
    expect(validateSourceForm(f, { creating: true })).toEqual({
      name: "Add a name.",
      key: "Enter a slug.",
      collection: "Choose the collection documents go into.",
    });
    expect(validateSourceForm({ ...f, name: "Site" }, { creating: false })).toEqual({});
  });

  it("round-trips a stored source into the edit form and back, threshold included", () => {
    const source: Source = sourceFixture({
      config: { start_urls: ["https://intranet.example/handbook/"], max_pages: 100, path_prefix: null },
      interval_minutes: 1440,
      stale_after_days: 90,
    });
    expect(toUpdatePayload(formFromSource(source))).toEqual({
      name: "Handbook site",
      config: { start_urls: ["https://intranet.example/handbook/"], max_pages: 100 },
      interval_minutes: 1440,
      enabled: true,
      stale_after_days: 90,
    });
    expect(toUpdatePayload(formFromSource({ ...source, stale_after_days: null })).stale_after_days).toBeNull();
  });

  it("maps API config fields back to form fields", () => {
    expect(configFieldKey("web", "start_urls")).toBe("web.startUrls");
    expect(configFieldKey("slack", "start_urls")).toBe("config");
    expect(configFieldKey("confluence", "site_url")).toBe("confluence.siteUrl");
    expect(configFieldKey("sharepoint", "site_url")).toBe("sharepoint.siteUrl");
    expect(configFieldKey("confluence", "spaces")).toBe("confluence.spaces");
  });

  it("round-trips Confluence and SharePoint sources into the edit form", () => {
    const wiki = sourceFixture({ kind: "confluence", config: { site_url: "https://acme.atlassian.net", spaces: ["HR"], include_attachments: true } });
    expect(toUpdatePayload(formFromSource(wiki)).config).toEqual({ site_url: "https://acme.atlassian.net", spaces: ["HR"], include_attachments: true });
    const library = sourceFixture({ kind: "sharepoint", config: { site_url: "https://contoso.sharepoint.com/sites/finance", library: null, folder: "Policies" } });
    expect(toUpdatePayload(formFromSource(library)).config).toEqual({ site_url: "https://contoso.sharepoint.com/sites/finance", folder: "Policies" });
    expect(whatSyncs(wiki)).toBe("Space HR on acme.atlassian.net");
    expect(whatSyncs(library)).toBe("contoso.sharepoint.com/sites/finance › Policies");
    expect(whatSyncs(sourceFixture({ kind: "sharepoint", config: {} }))).toBe("The connected account's OneDrive");
  });
});

describe("stats", () => {
  it("summarizes a sync", () => {
    expect(statsSummary({ added: 12, updated: 3, unchanged: 40, deleted: 0, failed: 1, items: 56 })).toBe("12 added · 3 updated · 40 unchanged · 1 failed");
    expect(statsSummary({ added: 0, unchanged: 0, items: 0 })).toBe("Nothing changed");
    expect(statsSummary({})).toBeNull();
    expect(statsSummary({ deleted: 2, kept_under_hold: 1, items: 9 })).toBe("2 deleted · 1 kept under legal hold");
  });
});

describe("Connect Google Drive", () => {
  it("lets the browser continue when the API redirects to Google", async () => {
    const fetcher = vi.fn(async () => ({ type: "opaqueredirect", ok: false, status: 0 }) as Response);
    await expect(sourcesApi.checkGoogleStart("drive-finance", fetcher)).resolves.toBeUndefined();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/admin/sources/drive-finance/oauth/google/start", expect.objectContaining({ redirect: "manual" }));
  });

  it("turns 409 oauth_not_configured into an ApiError to explain in place", async () => {
    const body = { title: "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first", status: 409, code: "oauth_not_configured" };
    const fetcher = vi.fn(async () => new Response(JSON.stringify(body), { status: 409, headers: { "Content-Type": "application/problem+json" } }));
    const error = await sourcesApi.checkGoogleStart("drive-finance", fetcher).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "oauth_not_configured" });
  });
});

describe("health and schedule", () => {
  const now = new Date("2026-11-13T08:30:00Z");

  it("says when the next sync is due", () => {
    expect(nextSyncText(sourceFixture(), now)).toBe("In 30 min");
    expect(nextSyncText(sourceFixture({ next_sync_at: "2026-11-13T08:00:00Z" }), now)).toBe("Due now (within a minute)");
    expect(nextSyncText(sourceFixture({ next_sync_at: "2026-11-14T08:30:00Z" }), now)).toBe("In 24 hours");
    expect(nextSyncText(sourceFixture({ enabled: false, next_sync_at: null }), now)).toBe("Paused");
    expect(nextSyncText(sourceFixture({ syncing: true, next_sync_at: null }), now)).toBe("Syncing now");
  });

  it("counts failures in a row and whether the managers were told", () => {
    expect(failureText(sourceFixture(), 3)).toBeNull();
    expect(failureText(sourceFixture({ consecutive_failures: 1 }), 3)).toBe("Failed once · they're notified after 3 in a row");
    expect(failureText(sourceFixture({ consecutive_failures: 4, failure_alerted_at: "2026-11-13T08:00:00Z" }), 3)).toBe(
      "Failed 4 times in a row · the people who manage it were notified",
    );
  });

  it("counts and sorts by health, failing first", () => {
    const sources = [
      sourceFixture({ name: "B", health: "healthy" }),
      sourceFixture({ name: "A", health: "paused" }),
      sourceFixture({ name: "C", health: "failing" }),
      sourceFixture({ name: "D", health: "never_synced" }),
    ];
    expect(healthCounts(sources)).toEqual({ healthy: 1, failing: 1, paused: 1, never_synced: 1 });
    expect(sortSources(sources).map((s) => s.name)).toEqual(["C", "D", "A", "B"]);
  });

  it("describes the mapping: what syncs, and who can see it", () => {
    expect(whatSyncs(sourceFixture())).toBe("https://intranet.example/handbook/");
    expect(whatSyncs(sourceFixture({ kind: "github", config: { repo: "acme/handbook", branch: "main" } }))).toBe("acme/handbook (main)");
    expect(whatSyncs(sourceFixture({ kind: "slack", config: { channels: ["C1", "C2"] } }))).toBe("2 Slack channels");
    expect(accessSummary(sourceFixture().access)).toBe("Alice Moreau (owner) · People & HR (department) can edit · Everyone in the organization can view");
    expect(accessSummary([])).toBe("Nobody but org admins");
  });

  it("summarizes runs: trigger, duration, and what changed", () => {
    expect(triggerLabel(runFixture({ trigger: "manual", requested_by: { id: "u", display_name: "Erin" } }))).toBe("Sync now by Erin");
    expect(triggerLabel(runFixture())).toBe("Scheduled");
    expect(runDuration(runFixture())).toBe("12 s");
    expect(runDuration(runFixture({ finished_at: "2026-11-13T08:02:05Z" }))).toBe("2 min 5 s");
    expect(runDuration(runFixture({ status: "running", finished_at: null }))).toBeNull();
    expect(changeCounts({ added: 2, deleted: 1 })).toEqual({ added: 2, updated: 0, deleted: 1 });
    expect(statsSummary({ added: 1, missing: 3, items: 5 })).toBe("1 added · 3 missing upstream");
  });
});

describe("stale threshold, disconnect, OAuth", () => {
  it("defaults by kind (Slack history is an archive) and survives a kind change", () => {
    expect(emptySourceForm("web").staleAfterDays).toBe(180);
    expect(emptySourceForm("slack").staleAfterDays).toBeNull();
    expect(withKind(emptySourceForm("web"), "slack").staleAfterDays).toBeNull();
    expect(staleAfterValue(null)).toBe("never");
    expect(staleAfterFromValue("never")).toBeNull();
    expect(staleAfterFromValue("365")).toBe(365);
    expect(staleAfterOptions(45).at(-1)).toEqual({ value: "45", label: "Unchanged for 45 days" });
    expect(staleAfterLabel(90)).toBe("Unchanged for 3 months");
  });

  it("a Slack source may skip the bot token when the deployment can Connect Slack", () => {
    const f = form("slack", { slack: { channels: "C0123ABCD", days: "30" } });
    expect(validateSourceForm(f, { creating: true })).toEqual({ "credentials.bot_token": "Slack needs a bot token." });
    expect(validateSourceForm(f, { creating: true, slackOAuth: true })).toEqual({});
  });

  it("connects first when OAuth has no tokens yet, else syncs at once", () => {
    expect(afterCreate({ kind: "gdrive", enabled: true, has_credentials: false, oauth: "google" })).toBe("connect");
    expect(afterCreate({ kind: "slack", enabled: true, has_credentials: false, oauth: "slack" })).toBe("connect");
    expect(afterCreate({ kind: "slack", enabled: true, has_credentials: true, oauth: "slack" })).toBe("sync");
    expect(afterCreate({ kind: "web", enabled: false, has_credentials: false, oauth: null })).toBe("nothing");
  });

  it("disconnects only after the exact key is typed", () => {
    expect(disconnectConfirmed("handbook-site", sourceFixture())).toBe(true);
    expect(disconnectConfirmed(" handbook-site ", sourceFixture())).toBe(true);
    expect(disconnectConfirmed("Handbook site", sourceFixture())).toBe(false);
    expect(disconnectConfirmed("", sourceFixture())).toBe(false);
  });

  it("reads the OAuth result for either provider", () => {
    expect(oauthResult(new URLSearchParams("oauth=connected&provider=slack&source=chat"))).toEqual({ ok: true, provider: "slack", source: "chat" });
    expect(oauthResult(new URLSearchParams("oauth=failed"))).toEqual({ ok: false, provider: "google", source: null });
    expect(oauthResult(new URLSearchParams("oauth=connected&provider=microsoft&source=finance-docs"))?.provider).toBe("microsoft");
    expect(oauthResult(new URLSearchParams("oauth=failed&provider=atlassian"))?.provider).toBe("atlassian");
    expect(oauthResult(new URLSearchParams("source=chat"))).toBeNull();
  });

  it("builds the start URL with where to come back", () => {
    expect(sourcesApi.oauthStartUrl("slack", "team chat", "knowledge")).toBe("/api/v1/admin/sources/team%20chat/oauth/slack/start?back=knowledge");
    expect(sourcesApi.oauthStartUrl("google", "drive")).toBe("/api/v1/admin/sources/drive/oauth/google/start");
  });

  it("explains stale reasons", () => {
    const format = (iso: string) => iso.slice(0, 10);
    expect(staleReasonText(staleFixture(), format)).toBe("Unchanged in the source since 2025-09-01");
    expect(staleReasonText(staleFixture({ reason: "missing_upstream", missing_since: "2026-11-10T00:00:00Z" }), format)).toBe(
      "Not listed by the source since 2026-11-10",
    );
  });
});
