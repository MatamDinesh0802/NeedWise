import type { Metadata } from "next";

import { AutomationSection } from "@/components/admin/automation-section";

export const metadata: Metadata = { title: "Automation · Admin" };

export default function AdminAutomationPage() {
  return <AutomationSection />;
}
