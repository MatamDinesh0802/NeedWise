import type { Metadata } from "next";

import { RetentionSection } from "@/components/admin/retention-section";

export const metadata: Metadata = { title: "Retention · Admin" };

export default function AdminRetentionPage() {
  return <RetentionSection />;
}
