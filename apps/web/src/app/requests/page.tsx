import { sectionMetadata } from "@/components/page-placeholder";
import { RequestsView } from "@/components/work/request-form";

export const metadata = sectionMetadata("/requests");

export default function RequestsPage() {
  return <RequestsView />;
}
