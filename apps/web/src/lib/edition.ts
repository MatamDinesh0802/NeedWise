/**
 * Which edition this deployment runs: GET /api/v1/edition (ADR 0011, docs/enterprise.md).
 * Community is the complete Apache-2.0 product; Enterprise adds licensed features.
 *
 * Cosmetic only: the API checks the license on every request and answers
 * 402 "enterprise_feature" on its own. The UI uses this to explain, not to protect.
 */

import { ApiError, apiJson } from "@/lib/api";

/** The Enterprise features the API knows about (workmind.core.edition.FEATURES). */
export type EnterpriseFeature =
  | "retention_policies"
  | "department_budgets"
  | "chargeback_reports"
  | "advanced_audit_export"
  | "legal_hold";

export const ENTERPRISE_FEATURES: Readonly<Record<EnterpriseFeature, { label: string; description: string }>> = {
  retention_policies: {
    label: "Retention policies",
    description: "Rules for how long data is kept before it is deleted.",
  },
  department_budgets: {
    label: "Department budgets",
    description: "Spending limits on model usage per department.",
  },
  chargeback_reports: {
    label: "Chargeback reports",
    description: "Usage and model costs per department, for internal billing.",
  },
  advanced_audit_export: {
    label: "Advanced audit export",
    description: "Audit log exports for security tools and long-term retention.",
  },
  legal_hold: {
    label: "Legal hold",
    description: "Keep documents, collections, people's conversations, meetings and Insights questions from being deleted while a legal matter is open.",
  },
};

export const ENTERPRISE_DOCS = "docs/enterprise.md";
export const ENTERPRISE_FEATURE_ERROR = "enterprise_feature";

export interface EditionInfo {
  edition: "community" | "enterprise";
  /** Licensed Enterprise features. Empty in Community. */
  features: string[];
  licensee: string | null;
  /** ISO 8601, or null in Community. */
  expires_at: string | null;
}

export const editionKey = "edition";

export const editionApi = {
  get: () => apiJson<EditionInfo>("/api/v1/edition"),
};

/** True only when the edition is known, is Enterprise, and licenses `feature`. */
export function hasFeature(edition: EditionInfo | null | undefined, feature: EnterpriseFeature): boolean {
  return edition?.edition === "enterprise" && edition.features.includes(feature);
}

export function featureLabel(feature: EnterpriseFeature): string {
  return ENTERPRISE_FEATURES[feature]?.label ?? feature;
}

/** The API's 402 for a feature this deployment isn't licensed for. */
export function isEnterpriseFeatureError(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 402 && error.code === ENTERPRISE_FEATURE_ERROR;
}

/** What to say instead of an unlicensed feature. */
export function enterpriseNotice(feature: EnterpriseFeature): { title: string; description: string } {
  const info = ENTERPRISE_FEATURES[feature];
  return {
    title: `Enterprise feature: ${featureLabel(feature)}`,
    description: `${info ? `${info.description} ` : ""}It needs a WorkMind Enterprise license, which this deployment doesn't have.`,
  };
}
