import { CollectionsView } from "@/components/knowledge/collections-view";
import { sectionMetadata } from "@/components/page-placeholder";

export const metadata = sectionMetadata("/knowledge");

export default function KnowledgePage() {
  return <CollectionsView />;
}
