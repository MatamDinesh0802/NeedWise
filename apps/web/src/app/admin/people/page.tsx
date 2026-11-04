import type { Metadata } from "next";

import { PeopleSection } from "@/components/admin/people-section";

export const metadata: Metadata = { title: "People · Admin" };

export default function AdminPeoplePage() {
  return <PeopleSection />;
}
