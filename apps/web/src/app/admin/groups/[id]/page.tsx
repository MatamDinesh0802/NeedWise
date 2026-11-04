import type { Metadata } from "next";
import { Suspense } from "react";

import { GroupDetail } from "@/components/admin/group-detail";
import { TableSkeleton } from "@/components/data/states";

export const metadata: Metadata = { title: "Group · Admin" };

// See admin/departments/[id]/page.tsx: useParams behind Suspense keeps the shell static.
export default function AdminGroupPage() {
  return (
    <Suspense fallback={<TableSkeleton />}>
      <GroupDetail />
    </Suspense>
  );
}
