import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { EditionInfo } from "@/lib/edition";

import { EnterpriseBadge, EnterpriseGateView, EnterpriseNotice } from "./enterprise-gate";

const community: EditionInfo = { edition: "community", features: [], licensee: null, expires_at: null };
const enterprise: EditionInfo = {
  edition: "enterprise",
  features: ["retention_policies"],
  licensee: "Acme Robotics",
  expires_at: "2026-12-12T00:00:00Z",
};

const secret = <p>Retention rules editor</p>;

describe("EnterpriseGateView", () => {
  it("says the license could not be checked, with a retry, when the edition request failed", () => {
    const failed = new Error("network down");
    const html = renderToStaticMarkup(
      <EnterpriseGateView feature="retention_policies" edition={null} error={failed} onRetry={() => {}}>
        {secret}
      </EnterpriseGateView>,
    );
    expect(html).not.toContain("Retention rules editor");
    expect(html).toContain("Couldn&#x27;t check the license");
    expect(html).toContain("Retry");
    expect(html).not.toContain("doesn&#x27;t have");
  });


  it("shows its children when the feature is licensed", () => {
    const html = renderToStaticMarkup(
      <EnterpriseGateView feature="retention_policies" edition={enterprise}>
        {secret}
      </EnterpriseGateView>,
    );
    expect(html).toBe("<p>Retention rules editor</p>");
  });

  it("explains instead when the feature is not licensed", () => {
    for (const [edition, feature] of [
      [community, "retention_policies"],
      [enterprise, "chargeback_reports"],
      [null, "retention_policies"],
    ] as const) {
      const html = renderToStaticMarkup(
        <EnterpriseGateView feature={feature} edition={edition}>
          {secret}
        </EnterpriseGateView>,
      );
      expect(html).not.toContain("Retention rules editor");
      expect(html).toContain("Enterprise feature: ");
      expect(html).toContain("docs/enterprise.md");
      expect(html).toContain(`data-feature="${feature}"`);
    }
  });

  it("shows neither while the edition is loading", () => {
    const html = renderToStaticMarkup(
      <EnterpriseGateView feature="retention_policies" edition={null} loading>
        {secret}
      </EnterpriseGateView>,
    );
    expect(html).not.toContain("Retention rules editor");
    expect(html).not.toContain("Enterprise feature");
    expect(html).toContain('aria-busy="true"');
  });
});

describe("EnterpriseNotice", () => {
  it("is a note, not an alarm, and names the feature", () => {
    const html = renderToStaticMarkup(<EnterpriseNotice feature="chargeback_reports" />);
    expect(html).toContain('role="note"');
    expect(html).not.toContain('role="alert"');
    expect(html).toContain("Enterprise feature: Chargeback reports");
    expect(html).toContain("<code");
  });
});

describe("EnterpriseBadge", () => {
  it("labels Enterprise-only UI, naming the feature when given", () => {
    expect(renderToStaticMarkup(<EnterpriseBadge />)).toContain("Enterprise");
    const html = renderToStaticMarkup(<EnterpriseBadge feature="advanced_audit_export" />);
    expect(html).toContain('aria-label="Advanced audit export is part of NeedWise Enterprise"');
    expect(html).toContain(">Enterprise</span>");
  });
});
