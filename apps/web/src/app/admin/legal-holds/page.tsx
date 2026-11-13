import type { Metadata } from "next";

import { LegalHoldsSection } from "@/components/admin/legal-holds-section";

export const metadata: Metadata = { title: "Legal holds · Admin" };

export default function AdminLegalHoldsPage() {
  return <LegalHoldsSection />;
}
