import { expect, type Locator, type Page } from "@playwright/test";

import { json } from "./fixtures";

/** GET /api/v1/edition (apps/api/src/workmind/api/v1/edition.py). */
export interface Edition {
  edition: "community" | "enterprise";
  features: readonly string[];
}

export type EnterpriseFeature = "retention_policies" | "department_budgets" | "chargeback_reports" | "advanced_audit_export" | "permission_sync" | "legal_hold";

const LABELS: Record<EnterpriseFeature, string> = {
  retention_policies: "Retention policies",
  department_budgets: "Department budgets",
  chargeback_reports: "Chargeback reports",
  advanced_audit_export: "Advanced audit export",
  permission_sync: "Permission sync",
  legal_hold: "Legal hold",
};

export async function editionOf(page: Page): Promise<Edition> {
  return json<Edition>(await page.request.get("/api/v1/edition"));
}

export function licenses(edition: Edition, feature: EnterpriseFeature): boolean {
  return edition.edition === "enterprise" && edition.features.includes(feature);
}

/**
 * EnterpriseGate: with a license that includes `feature`, `content` shows and no notice; without
 * one (Community, as in CI), the upgrade notice shows instead and `content` is not there. The
 * same test passes in both editions.
 */
export async function expectGate(page: Page, edition: Edition, feature: EnterpriseFeature, content: Locator): Promise<void> {
  const notice = page.locator(`[role="note"][data-feature="${feature}"]`);
  if (licenses(edition, feature)) {
    await expect(content.first()).toBeVisible();
    await expect(notice).toHaveCount(0);
  } else {
    await expect(notice).toBeVisible();
    await expect(notice).toContainText(`Enterprise feature: ${LABELS[feature]}`);
    await expect(notice).toContainText("docs/enterprise.md");
    await expect(content).toHaveCount(0);
  }
}
