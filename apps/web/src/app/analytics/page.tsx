import { sectionMetadata } from "@/components/page-placeholder";
import { UsageView } from "@/components/usage/usage-view";

export const metadata = sectionMetadata("/analytics");

export default function UsagePage() {
  return <UsageView />;
}
