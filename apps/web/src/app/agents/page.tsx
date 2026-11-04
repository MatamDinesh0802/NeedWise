import { AgentsDirectory } from "@/components/work/agents-directory";
import { sectionMetadata } from "@/components/page-placeholder";

export const metadata = sectionMetadata("/agents");

export default function AgentsPage() {
  return <AgentsDirectory />;
}
