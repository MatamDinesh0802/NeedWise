import type { Metadata } from "next";
import { Suspense } from "react";

import { TableSkeleton } from "@/components/data/states";
import { CollectionView } from "@/components/knowledge/collection-view";

export const metadata: Metadata = { title: "Collection · Knowledge Hub" };

// The id is runtime data: the client component reads it with useParams behind Suspense,
// so the page shell stays static.
export default function CollectionPage() {
  return (
    <Suspense fallback={<TableSkeleton />}>
      <CollectionView />
    </Suspense>
  );
}
