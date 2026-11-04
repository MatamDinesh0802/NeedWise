import type { Metadata } from "next";
import { Suspense } from "react";

import { DepartmentDetail } from "@/components/admin/department-detail";
import { TableSkeleton } from "@/components/data/states";

export const metadata: Metadata = { title: "Department · Admin" };

// The id is only known at request time; the client component reads it with useParams,
// so the page shell stays static and the detail streams in behind Suspense.
export default function AdminDepartmentPage() {
  return (
    <Suspense fallback={<TableSkeleton />}>
      <DepartmentDetail />
    </Suspense>
  );
}
