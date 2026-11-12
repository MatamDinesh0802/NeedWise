import { MeetingsView } from "@/components/meetings/meetings-view";
import { sectionMetadata } from "@/components/page-placeholder";

export const metadata = sectionMetadata("/meetings");

export default function MeetingsPage() {
  return <MeetingsView />;
}
