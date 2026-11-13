import type { Metadata } from "next";

import { OwnedSourcesView } from "@/components/knowledge/owned-sources-view";

export const metadata: Metadata = { title: "Sources · Knowledge Hub" };

/** For collection owners: the sources that sync into the collections they own. */
export default function KnowledgeSourcesPage() {
  return <OwnedSourcesView />;
}
