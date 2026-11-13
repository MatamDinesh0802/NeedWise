import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Dialog } from "@/components/ui/dialog";
import type { Source } from "@/lib/admin/sources";
import { runFixture, sourceFixture } from "@/test/sources-fixtures";

import { DisconnectForm, HealthSummary, SourceCard } from "./sources-section";

const NOW = new Date("2026-11-13T08:30:00Z");
const noop = () => {};

function card(source: Source, extra: Partial<Parameters<typeof SourceCard>[0]> = {}): string {
  return renderToStaticMarkup(
    <SourceCard
      source={source}
      scope="all"
      failureThreshold={3}
      oauthConfigured={null}
      back="admin"
      onEdit={noop}
      onCredentials={noop}
      onDisconnect={noop}
      onDetail={noop}
      onSynced={noop}
      now={NOW}
      {...extra}
    />,
  );
}

/** The attributes of the button whose text contains `label`. */
function buttonAttrs(html: string, label: string): string {
  const match = [...html.matchAll(/<button([^>]*)>((?:(?!<\/button>).)*)<\/button>/g)].find((m) => m[2].includes(label));
  if (!match) throw new Error(`no button with ${label}`);
  return match[1];
}

describe("SourceCard", () => {
  it("shows a healthy source: mapping, last success, next sync and what the last run changed", () => {
    const html = card(sourceFixture());
    expect(html).toContain("Healthy");
    expect(html).toContain("https://intranet.example/handbook/");
    expect(html).toContain('href="/knowledge/collections/c-handbook"');
    expect(html).toContain("visible to Alice Moreau (owner) · People &amp; HR (department) can edit · Everyone in the organization can view");
    expect(html).toContain('href="/admin/collections"'); // admins can change who sees it
    expect(html).toContain("In 30 min");
    expect(html).toContain("2 added · 1 updated · 11 unchanged");
    expect(html).toContain("set up by Erin Takahashi");
    expect(buttonAttrs(html, "Sync now")).not.toContain('disabled=""');
    expect(html).toContain(">Pause<");
    expect(html).not.toContain("Recent errors");
  });

  it("explains a failing source in plain words, with the raw message behind a disclosure", () => {
    const html = card(
      sourceFixture({
        health: "failing",
        status: "error",
        consecutive_failures: 3,
        failure_alerted_at: "2026-11-13T08:00:00Z",
        last_error: "https://intranet.example/handbook/ is unreachable: ConnectError",
        last_error_plain: "NeedWise couldn't reach the source: it may be down, or blocked from this server.",
        last_run: runFixture({ status: "error", stats: {}, errors: [] }),
      }),
    );
    expect(html).toContain("Failing");
    expect(html).toContain('aria-label="Recent errors"');
    expect(html).toContain("NeedWise couldn&#x27;t reach the source");
    expect(html).toContain("Failed 3 times in a row · the people who manage it were notified");
    expect(html).toMatch(/<details[^>]*><summary[^>]*>What the source said<\/summary><p[^>]*>https:\/\/intranet.example\/handbook\/ is unreachable/);
  });

  it("a paused source can be resumed but not synced", () => {
    const html = card(sourceFixture({ enabled: false, health: "paused", next_sync_at: null }));
    expect(html).toContain("Paused");
    expect(html).toContain(">Resume<");
    expect(buttonAttrs(html, "Sync now")).toContain('disabled=""');
  });

  it("offers Connect for OAuth kinds, and never shows credentials", () => {
    const drive = card(sourceFixture({ kind: "gdrive", oauth: "google", config: { folder_id: "1AbCdEfGhIjK" }, needs: ["refresh_token"], health: "never_synced", last_run: null }));
    expect(drive).toContain("Connect Google Drive");
    expect(drive).toContain("Not connected to Google yet");
    expect(drive).toContain("Never synced");
    expect(drive).not.toContain(">Credentials<");

    const slack = card(sourceFixture({ kind: "slack", oauth: "slack", config: { channels: ["C0123ABCD"] }, needs: ["bot_token"], has_credentials: true }), {
      oauthConfigured: true,
    });
    expect(slack).toContain("Reconnect Slack");
    expect(slack).toContain(">Credentials<");
    expect(slack).toContain("Slack bot token stored (encrypted, never shown)");

    const noOAuth = card(sourceFixture({ kind: "slack", oauth: "slack", config: { channels: ["C0123ABCD"] }, needs: ["bot_token"] }), { oauthConfigured: false });
    expect(noOAuth).not.toContain("Connect Slack");
  });

  it("Confluence and SharePoint: Connect, and SharePoint offers it even before the server is set up", () => {
    const wiki = card(sourceFixture({ kind: "confluence", oauth: "atlassian", config: { site_url: "https://acme.atlassian.net", spaces: ["HR"] }, needs: ["email", "api_token"] }), {
      oauthConfigured: true,
    });
    expect(wiki).toContain("Space HR on acme.atlassian.net");
    expect(wiki).toContain("Connect Confluence");
    expect(wiki).toContain(">Credentials<");
    expect(wiki).toContain("Not connected to Confluence yet");

    const library = card(sourceFixture({ kind: "sharepoint", oauth: "microsoft", config: {}, needs: ["refresh_token"] }), { oauthConfigured: false });
    expect(library).toContain("Connect SharePoint");
    expect(library).toContain("Not connected to Microsoft yet");
    expect(library).not.toContain(">Credentials<");
  });

  it("says when permission sync narrows a source's documents, and how many accounts map to nobody", () => {
    const drive = card(
      sourceFixture({
        kind: "gdrive",
        oauth: "google",
        config: { folder_id: "1AbCdEfGhIjK" },
        has_credentials: true,
        permission_sync: true,
        last_run: runFixture({ stats: { items: 3, unchanged: 3, acl_changed: 1, acl_unmapped: 2 } }),
      }),
    );
    expect(drive).toContain("Permission sync:");
    expect(drive).toContain("each file&#x27;s sharing in Drive");
    expect(drive).toContain("2 accounts the source named map to nobody in NeedWise yet (Source accounts, below)");
    expect(card(sourceFixture())).not.toContain("Permission sync:");
  });

  it("links stale documents to the detail panel, and owners don't get the admin sharing link", () => {
    const html = card(sourceFixture({ stale: { not_updated: 2, missing_upstream: 1 } }), { scope: "owned" });
    expect(html).toContain("3 stale documents");
    expect(html).toContain("(1 missing upstream, 2 unchanged past the threshold)");
    expect(html).not.toContain('href="/admin/collections"');
  });

  it("shows a sync in progress", () => {
    const html = card(sourceFixture({ syncing: true, status: "syncing", next_sync_at: null }));
    expect(html).toContain("Syncing…");
    expect(html).toContain("Syncing now");
    expect(buttonAttrs(html, "Sync now")).toContain('disabled=""');
  });
});

describe("HealthSummary", () => {
  it("counts each health as a filter toggle", () => {
    const html = renderToStaticMarkup(
      <HealthSummary sources={[sourceFixture(), sourceFixture({ id: "s2", health: "failing" })]} filter="failing" onFilter={noop} />,
    );
    expect(html).toContain('aria-label="Filter sources by health"');
    expect(buttonAttrs(html, "All 2")).toContain('aria-pressed="false"');
    expect(buttonAttrs(html, "Failing 1")).toContain('aria-pressed="true"');
    expect(buttonAttrs(html, "Paused 0")).toContain('disabled=""');
  });
});

describe("DisconnectForm", () => {
  it("says what is deleted and waits for the typed key", () => {
    const html = renderToStaticMarkup(
      <Dialog open>
        <DisconnectForm source={sourceFixture()} onDone={noop} />
      </Dialog>,
    );
    expect(html).toContain("Disconnect Handbook site?");
    expect(html).toContain("deletes the 14 documents it synced into Company Handbook");
    expect(html).toContain("Nothing changes in the source itself.");
    expect(html).toContain("Type “handbook-site” to confirm");
    expect(buttonAttrs(html, "Disconnect and delete documents")).toContain('disabled=""');
  });
});
