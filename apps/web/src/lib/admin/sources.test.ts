import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import {
  configFieldKey,
  configPayload,
  driveFolderId,
  emptySourceForm,
  formFromSource,
  sourcesApi,
  statsSummary,
  toCreatePayload,
  toggledSource,
  toUpdatePayload,
  validateSourceForm,
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

  it("validates key, name and collection on create only", () => {
    const f = { ...emptySourceForm("web"), web: { startUrls: "https://x.example/", maxPages: "10", pathPrefix: "" } };
    expect(validateSourceForm(f, { creating: true })).toEqual({
      name: "Add a name.",
      key: "Enter a slug.",
      collection: "Choose the collection documents go into.",
    });
    expect(validateSourceForm({ ...f, name: "Site" }, { creating: false })).toEqual({});
  });

  it("round-trips a stored source into the edit form and back", () => {
    const source: Source = {
      id: "s1",
      key: "handbook-site",
      name: "Handbook site",
      kind: "web",
      collection: "company-handbook",
      config: { start_urls: ["https://intranet.example/handbook/"], max_pages: 100, path_prefix: null },
      has_credentials: false,
      needs: [],
      interval_minutes: 1440,
      enabled: true,
      status: "ok",
      last_sync_at: null,
      last_success_at: null,
      last_error: null,
      stats: {},
      documents: 12,
    };
    expect(toUpdatePayload(formFromSource(source))).toEqual({
      name: "Handbook site",
      config: { start_urls: ["https://intranet.example/handbook/"], max_pages: 100 },
      interval_minutes: 1440,
      enabled: true,
    });
    expect(toggledSource(source, false)).toEqual({ name: "Handbook site", config: source.config, interval_minutes: 1440, enabled: false });
  });

  it("maps API config fields back to form fields", () => {
    expect(configFieldKey("web", "start_urls")).toBe("web.startUrls");
    expect(configFieldKey("slack", "start_urls")).toBe("config");
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
