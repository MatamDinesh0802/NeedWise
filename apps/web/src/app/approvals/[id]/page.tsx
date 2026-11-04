import type { Metadata } from "next";
import { Suspense } from "react";

import { TableSkeleton } from "@/components/data/states";
import { ApprovalPage } from "@/components/work/approvals-inbox";

export const metadata: Metadata = { title: "Approval · Approvals" };

// The id is runtime data: read with useParams behind Suspense so the shell stays static.
export default function ApprovalDetailPage() {
  return (
    <Suspense fallback={<TableSkeleton rows={2} />}>
      <ApprovalPage />
    </Suspense>
  );
}
