import type { Metadata } from "next";

import { ModelsSection } from "@/components/admin/models-section";

export const metadata: Metadata = { title: "Models · Admin" };

export default function AdminModelsPage() {
  return <ModelsSection />;
}
