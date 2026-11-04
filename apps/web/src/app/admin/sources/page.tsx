import type { Metadata } from "next";

import { SourcesSection } from "@/components/admin/sources-section";

export const metadata: Metadata = { title: "Sources · Admin" };

export default function AdminSourcesPage() {
  return <SourcesSection />;
}
