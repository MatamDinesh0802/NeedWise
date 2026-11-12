import type { Metadata } from "next";

import { GovernanceSection } from "@/components/admin/governance-section";

export const metadata: Metadata = { title: "Governance · Admin" };

export default function AdminGovernancePage() {
  return <GovernanceSection />;
}
