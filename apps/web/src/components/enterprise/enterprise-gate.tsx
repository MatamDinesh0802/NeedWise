"use client";

import { Gem } from "lucide-react";
import type { ReactNode } from "react";

import { ErrorAlert } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useEdition } from "@/hooks/use-edition";
import { cn } from "@/lib/utils";
import {
  ENTERPRISE_DOCS,
  enterpriseNotice,
  featureLabel,
  hasFeature,
  type EditionInfo,
  type EnterpriseFeature,
} from "@/lib/edition";

/**
 * Shows `children` only when this deployment's license includes `feature`; otherwise a short
 * explanation. Cosmetic: the API refuses unlicensed requests (402) on its own.
 */
export function EnterpriseGate({ feature, children }: { feature: EnterpriseFeature; children: ReactNode }) {
  const { edition, loading, error, retry } = useEdition();
  return (
    <EnterpriseGateView feature={feature} edition={edition} loading={loading} error={error} onRetry={retry}>
      {children}
    </EnterpriseGateView>
  );
}

/** The license could not be checked: say so, with a retry, never "this deployment doesn't have it". */
export const EDITION_UNKNOWN = {
  title: "Couldn't check the license",
  description: "Whether this deployment includes this Enterprise feature is not known right now. Try again in a moment.",
  code: null,
};

/** The gate without fetching: for tests and for pages that already have the edition. */
export function EnterpriseGateView({
  feature,
  edition,
  loading = false,
  error = null,
  onRetry,
  children,
}: {
  feature: EnterpriseFeature;
  edition: EditionInfo | null;
  loading?: boolean;
  /** The edition request failed: the feature may well be licensed. */
  error?: unknown;
  onRetry?: () => void;
  children: ReactNode;
}) {
  if (loading) return <Skeleton className="h-16 w-full" aria-busy="true" aria-label="Checking the license" />;
  if (hasFeature(edition, feature)) return <>{children}</>;
  if (edition === null && error) return <ErrorAlert error={error} message={EDITION_UNKNOWN} onRetry={onRetry} className="sm:max-w-xl" />;
  return <EnterpriseNotice feature={feature} />;
}

/** "Enterprise feature: …; see docs/enterprise.md". Never an error: Community is complete. */
export function EnterpriseNotice({ feature, className }: { feature: EnterpriseFeature; className?: string }) {
  const notice = enterpriseNotice(feature);
  return (
    <Alert role="note" data-feature={feature} className={cn("sm:max-w-xl", className)}>
      <Gem aria-hidden="true" />
      <AlertTitle>{notice.title}</AlertTitle>
      <AlertDescription>
        <p>
          {notice.description} See <code className="font-mono text-xs">{ENTERPRISE_DOCS}</code>.
        </p>
      </AlertDescription>
    </Alert>
  );
}

/** Marks Enterprise-only UI, e.g. next to a section title. */
export function EnterpriseBadge({ feature, className }: { feature?: EnterpriseFeature; className?: string }) {
  const title = feature ? `${featureLabel(feature)} is part of NeedWise Enterprise` : "Part of NeedWise Enterprise";
  return (
    <Badge variant="outline" title={title} aria-label={title} className={className}>
      <Gem aria-hidden="true" data-icon="inline-start" />
      Enterprise
    </Badge>
  );
}
