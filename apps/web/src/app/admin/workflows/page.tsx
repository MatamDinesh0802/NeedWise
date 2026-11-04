import type { Metadata } from "next";

import { WorkflowsSection } from "@/components/admin/workflows-section";

export const metadata: Metadata = { title: "Workflows · Admin" };

export default function AdminWorkflowsPage() {
  return <WorkflowsSection />;
}
