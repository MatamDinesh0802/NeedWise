import type { Metadata } from "next";

import { OwnedGovernanceView } from "@/components/knowledge/owned-governance-view";

export const metadata: Metadata = { title: "Governance · Knowledge Hub" };

/** For collection owners who aren't administrators: the overview of the collections they own. */
export default function KnowledgeGovernancePage() {
  return <OwnedGovernanceView />;
}
