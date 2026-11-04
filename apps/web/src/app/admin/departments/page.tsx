import type { Metadata } from "next";

import { DepartmentsSection } from "@/components/admin/departments-section";

export const metadata: Metadata = { title: "Departments · Admin" };

export default function AdminDepartmentsPage() {
  return <DepartmentsSection />;
}
