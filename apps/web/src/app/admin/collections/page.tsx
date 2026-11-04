import type { Metadata } from "next";

import { CollectionsSection } from "@/components/admin/collections-section";

export const metadata: Metadata = { title: "Collections · Admin" };

export default function AdminCollectionsPage() {
  return <CollectionsSection />;
}
