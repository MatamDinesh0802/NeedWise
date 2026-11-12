import type { Metadata } from "next";

import { BudgetsSection } from "@/components/admin/budgets-section";

export const metadata: Metadata = { title: "Budgets · Admin" };

export default function AdminBudgetsPage() {
  return <BudgetsSection />;
}
