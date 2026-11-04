import { sectionMetadata } from "@/components/page-placeholder";
import { ApprovalsInbox } from "@/components/work/approvals-inbox";

export const metadata = sectionMetadata("/approvals");

export default function ApprovalsPage() {
  return <ApprovalsInbox />;
}
