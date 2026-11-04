import type { Metadata } from "next";

import { AgentsSection } from "@/components/admin/agents-section";

export const metadata: Metadata = { title: "Agents · Admin" };

export default function AdminAgentsPage() {
  return <AgentsSection />;
}
