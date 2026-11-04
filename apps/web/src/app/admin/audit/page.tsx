import type { Metadata } from "next";

import { AuditSection } from "@/components/admin/audit-section";

export const metadata: Metadata = { title: "Audit log · Admin" };

export default function AdminAuditPage() {
  return <AuditSection />;
}
