import type { Metadata } from "next";

import { GroupsSection } from "@/components/admin/groups-section";

export const metadata: Metadata = { title: "Groups · Admin" };

export default function AdminGroupsPage() {
  return <GroupsSection />;
}
