import { Suspense } from "react";

import { InsightsView } from "@/components/insights/insights-view";
import { sectionMetadata } from "@/components/page-placeholder";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata = sectionMetadata("/insights");

// ?run=<id> is runtime data (useSearchParams): read it behind Suspense so the shell stays static.
export default function InsightsPage() {
  return (
    <Suspense fallback={<Skeleton className="mx-auto h-40 w-full max-w-6xl rounded-2xl" />}>
      <InsightsView />
    </Suspense>
  );
}
