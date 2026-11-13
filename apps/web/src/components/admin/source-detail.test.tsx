import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { runFixture, staleFixture } from "@/test/sources-fixtures";

import { RunList, StaleList } from "./source-detail";

const NOW = new Date("2026-11-13T08:30:00Z");

describe("RunList", () => {
  it("lists runs with their trigger, duration, counts and plain errors", () => {
    const html = renderToStaticMarkup(
      <RunList
        now={NOW}
        runs={[
          runFixture({
            id: "r2",
            status: "error",
            trigger: "manual",
            requested_by: { id: "u-erin", display_name: "Erin Takahashi" },
            stats: { items: 0 },
            errors: [{ item: null, message: "Slack conversations.history: not_in_channel", plain: "The Slack app isn't a member of one of the channels." }],
          }),
          runFixture({ id: "r1", complete: false, stats: { items: 5, added: 1, unchanged: 4, missing: 2 } }),
        ]}
      />,
    );
    expect(html).toContain('aria-label="Runs, newest first"');
    expect(html).toContain("Failed");
    expect(html).toContain("Sync now by Erin Takahashi · took 12 s");
    expect(html).toContain("The Slack app isn&#x27;t a member of one of the channels.");
    expect(html).toContain("Slack conversations.history: not_in_channel");
    expect(html).toContain("Succeeded");
    expect(html).toMatch(/<dt[^>]*>Added<\/dt><dd[^>]*>1<\/dd>/);
    expect(html).toContain("1 added · 4 unchanged · 2 missing upstream");
    expect(html).toContain("so nothing was deleted");
  });

  it("has an empty state", () => {
    expect(renderToStaticMarkup(<RunList runs={[]} />)).toContain("No runs yet");
  });
});

describe("StaleList", () => {
  it("links each document and its original, and names the reason", () => {
    const html = renderToStaticMarkup(
      <StaleList
        showSource
        empty="Nothing looks stale."
        items={[staleFixture({ reason: "missing_upstream", missing_since: "2026-11-10T00:00:00Z", reminded_at: "2026-11-11T00:00:00Z" }), staleFixture({ id: "d-2", title: "Leave policy" })]}
      />,
    );
    expect(html).toContain('href="/knowledge/documents/d-travel"');
    expect(html).toContain('href="https://intranet.example/handbook/travel.html"');
    expect(html).toContain('rel="noreferrer noopener"');
    expect(html).toContain("Missing upstream");
    expect(html).toContain("Not updated");
    expect(html).toContain("Handbook site");
    expect(html).toContain("Owner: Erin Takahashi");
    expect(html).toContain("reminded");
  });

  it("withholds the title and link of a document the viewer can't open", () => {
    const html = renderToStaticMarkup(<StaleList showSource={false} empty="" items={[staleFixture({ title: null, url: null, collection_name: "HR Guidance (internal)" })]} />);
    expect(html).toContain("A document in “HR Guidance (internal)” you can&#x27;t open");
    expect(html).not.toContain("/knowledge/documents/");
    expect(html).not.toContain("Open in the source");
  });

  it("says when nothing is stale", () => {
    expect(renderToStaticMarkup(<StaleList showSource empty="Nothing looks stale." items={[]} />)).toContain("Nothing looks stale.");
  });
});
