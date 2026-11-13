import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { EnterpriseGateView } from "@/components/enterprise/enterprise-gate";
import { Dialog } from "@/components/ui/dialog";
import type { LegalHold } from "@/lib/admin/legal-holds";
import type { EditionInfo } from "@/lib/edition";

import { LegalHoldsView, PlaceHoldForm, ReleaseHoldForm } from "./legal-holds-section";

const erin = { id: "u-erin", display_name: "Erin Takahashi" };

const hold = (overrides: Partial<LegalHold> = {}): LegalHold => ({
  id: "h-1",
  matter: "Smith v. Acme",
  reason: "Preserve Bob's records for discovery",
  target: { type: "user", id: "u-bob", label: "Bob Okafor (bob@acme.test)", exists: true },
  covers: { documents: 1, conversations: 12, meetings: 1, analytics_runs: 0 },
  placed_by: erin,
  placed_at: "2026-11-10T09:00:00Z",
  active: true,
  released_at: null,
  released_by: null,
  release_reason: null,
  ...overrides,
});

describe("LegalHoldsView", () => {
  it("groups active holds by matter and says what each keeps", () => {
    const html = renderToStaticMarkup(
      <LegalHoldsView
        holds={[
          hold(),
          hold({
            id: "h-2",
            matter: "Audit 2026",
            target: { type: "document", id: "d-1", label: "A document in “HR Guidance (internal)” you can't open", exists: true },
            covers: { documents: 1, conversations: 0, meetings: 0, analytics_runs: 0 },
          }),
        ]}
      />,
    );
    expect(html.indexOf("Audit 2026")).toBeLessThan(html.indexOf("Smith v. Acme"));
    expect(html).toContain("Bob Okafor (bob@acme.test)");
    expect(html).toContain("1 document, 12 conversations and 1 meeting");
    expect(html).toContain("A person");
    expect(html).toContain("by Erin Takahashi");
    expect(html).toContain("Release…");
    expect(html).toContain("Place a hold…");
  });

  it("lists released holds apart, with why they ended and no release button", () => {
    const html = renderToStaticMarkup(
      <LegalHoldsView
        holds={[hold({ active: false, released_at: "2026-11-12T09:00:00Z", released_by: erin, release_reason: "Case settled" })]}
      />,
    );
    expect(html).toContain("No active holds");
    expect(html).toContain("Released: Case settled");
    expect(html).toContain("released 12 Nov 2026 by Erin Takahashi");
    expect(html).not.toContain("Release…");
  });
});

describe("PlaceHoldForm", () => {
  it("asks for the matter, what to hold and why", () => {
    const html = renderToStaticMarkup(
      <Dialog open>
        <PlaceHoldForm onDone={() => {}} initial={{ matter: "", reason: "", target_type: "meeting", target: "" }} />
      </Dialog>,
    );
    expect(html).toContain("Place a legal hold");
    expect(html).toMatch(/<label[^>]*>Matter<\/label>/);
    expect(html).toContain("Meeting: id or link");
    expect(html).toContain("Copy it from the meeting&#x27;s page: /meetings/…");
    expect(html).toContain("That meeting and its transcript.");
    expect(html).toMatch(/<label[^>]*>Why<\/label>/);
  });
});

describe("ReleaseHoldForm", () => {
  it("says what becomes deletable again and asks why", () => {
    const html = renderToStaticMarkup(
      <Dialog open>
        <ReleaseHoldForm hold={hold()} onDone={() => {}} />
      </Dialog>,
    );
    expect(html).toContain("Release this hold?");
    expect(html).toContain("what it kept (1 document, 12 conversations and 1 meeting) can be deleted again");
    expect(html).toContain("Why it ends");
  });
});

describe("without a license", () => {
  it("shows the Enterprise notice instead of the holds", () => {
    const community: EditionInfo = { edition: "community", features: [], licensee: null, expires_at: null };
    const html = renderToStaticMarkup(
      <EnterpriseGateView feature="legal_hold" edition={community}>
        <LegalHoldsView holds={[hold()]} />
      </EnterpriseGateView>,
    );
    expect(html).toContain("Enterprise feature: Legal hold");
    expect(html).not.toContain("Place a hold");
  });
});
