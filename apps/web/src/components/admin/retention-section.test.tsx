import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { EnterpriseGateView } from "@/components/enterprise/enterprise-gate";
import { Dialog } from "@/components/ui/dialog";
import type { RetentionPolicy, RetentionPreview } from "@/lib/admin/retention";
import type { EditionInfo } from "@/lib/edition";

import { PreviewResult, RetentionEditor, RunForm } from "./retention-section";

const policy = (overrides: Partial<RetentionPolicy> = {}): RetentionPolicy => ({
  conversations_days: 365,
  analytics_runs_days: null,
  meeting_recordings_days: 90,
  read_notifications_days: 30,
  minimum_days: 30,
  updated_at: "2026-11-01T09:00:00Z",
  updated_by: { id: "u-erin", display_name: "Erin Takahashi" },
  last_run_at: "2026-11-12T02:00:00Z",
  last_run: { trigger: "schedule", deleted: { conversations: 3, read_notifications: 12 }, files: 0 },
  ...overrides,
});

/** The attributes of the button whose text contains `label`. */
function buttonAttrs(html: string, label: string): string {
  const match = [...html.matchAll(/<button([^>]*)>((?:(?!<\/button>).)*)<\/button>/g)].find((m) => m[2].includes(label));
  if (!match) throw new Error(`no button with ${label}`);
  return match[1];
}

const preview: RetentionPreview = {
  as_of: "2026-11-12T12:00:00Z",
  cutoffs: { conversations: "2025-11-12T12:00:00Z", analytics_runs: null, meeting_recordings: "2026-08-14T12:00:00Z", read_notifications: "2026-10-13T12:00:00Z" },
  would_delete: { conversations: 2, analytics_runs: 0, meeting_recordings: 1, read_notifications: 40 },
};

describe("RetentionEditor", () => {
  it("labels every period, shows who saved it and what the last run deleted", () => {
    const html = renderToStaticMarkup(<RetentionEditor policy={policy()} />);
    expect(html).toMatch(/<label[^>]*for="([^"]+)"[^>]*>Conversations \(days\)<\/label><input[^>]*id="\1"[^>]*value="365"/);
    expect(html).toMatch(/<label[^>]*for="([^"]+)"[^>]*>Insights questions \(days\)<\/label><input[^>]*id="\1"[^>]*placeholder="Keep forever"[^>]*value=""/);
    expect(html).toContain("by Erin Takahashi");
    expect(html).toContain("(the daily run): deleted 3 conversations and 12 read notifications.");
    expect(html).toContain("Everything is saved.");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Save policy<\/button>/);
  });

  it("offers a run only for a saved policy that deletes something", () => {
    const saved = renderToStaticMarkup(<RetentionEditor policy={policy()} />);
    expect(buttonAttrs(saved, "Run the saved policy")).not.toContain('disabled=""');
    const empty = renderToStaticMarkup(
      <RetentionEditor
        policy={policy({ updated_at: null, updated_by: null, conversations_days: null, meeting_recordings_days: null, read_notifications_days: null, last_run_at: null, last_run: {} })}
      />,
    );
    expect(empty).toContain("No policy is saved yet, so nothing is deleted.");
    expect(empty).toContain("The saved policy keeps everything, so a run would delete nothing.");
    expect(buttonAttrs(empty, "Run the saved policy")).toContain('disabled=""');
    expect(empty).toContain("It has not run yet.");
  });
});

describe("PreviewResult", () => {
  it("shows exact counts, the cutoffs, and that nothing was deleted", () => {
    const html = renderToStaticMarkup(<PreviewResult preview={preview} draft />);
    expect(html).toContain("If you saved these changes, a run now would delete");
    expect(html).toContain("2 conversations, 1 meeting recording and 40 read notifications");
    expect(html).toContain("Kept forever");
    expect(html).toContain("Nothing was deleted.");
    expect(html).not.toContain("Kept (legal hold)");
  });

  it("says what a legal hold keeps", () => {
    const html = renderToStaticMarkup(
      <PreviewResult preview={{ ...preview, kept_under_hold: { conversations: 3, analytics_runs: 0, meeting_recordings: 0, read_notifications: 0 } }} draft={false} />,
    );
    expect(html).toContain("A legal hold keeps 3 conversations that would otherwise go.");
    expect(html).toContain("Kept (legal hold)");
  });
});

describe("RunForm", () => {
  it("asks for the typed confirmation, and keeps the button disabled until then", () => {
    const html = renderToStaticMarkup(
      <Dialog open>
        <RunForm onDone={() => {}} />
      </Dialog>,
    );
    expect(html).toContain("Type “delete” to confirm");
    expect(html).toContain("This can&#x27;t be undone.");
    expect(buttonAttrs(html, "Delete expired data")).toContain('disabled=""');
  });

  it("reports what a run deleted", () => {
    const html = renderToStaticMarkup(
      <Dialog open>
        <RunForm
          onDone={() => {}}
          initialResult={{ as_of: "2026-11-12T12:00:00Z", deleted: { conversations: 2, analytics_runs: 0, meeting_recordings: 1, read_notifications: 0 }, files_deleted: 1 }}
        />
      </Dialog>,
    );
    expect(html).toContain("Deleted 2 conversations and 1 meeting recording; 1 recording files removed from storage.");
    expect(html).toContain("0 Insights questions");
  });
});

describe("without a license", () => {
  it("shows the Enterprise notice instead of the policy", () => {
    const community: EditionInfo = { edition: "community", features: [], licensee: null, expires_at: null };
    const html = renderToStaticMarkup(
      <EnterpriseGateView feature="retention_policies" edition={community}>
        <RetentionEditor policy={policy()} />
      </EnterpriseGateView>,
    );
    expect(html).toContain("Enterprise feature: Retention policies");
    expect(html).not.toContain("Save policy");
  });
});
