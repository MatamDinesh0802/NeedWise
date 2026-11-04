import { PagePlaceholder, sectionMetadata } from "@/components/page-placeholder";
import { SystemStatusCard } from "@/components/system-status-card";

export const metadata = sectionMetadata("/");

export default function OverviewPage() {
  return (
    <PagePlaceholder href="/">
      <SystemStatusCard />
    </PagePlaceholder>
  );
}
