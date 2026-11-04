import type { Metadata } from "next";
import { Suspense } from "react";

import { TableSkeleton } from "@/components/data/states";
import { DocumentView } from "@/components/knowledge/document-view";

export const metadata: Metadata = { title: "Document · Knowledge Hub" };

// See knowledge/collections/[id]/page.tsx.
export default function DocumentPage() {
  return (
    <Suspense fallback={<TableSkeleton />}>
      <DocumentView />
    </Suspense>
  );
}
