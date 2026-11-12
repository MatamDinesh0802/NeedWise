import type { Metadata } from "next";

import { AnalyticsSection } from "@/components/admin/analytics-section";

export const metadata: Metadata = { title: "Analytics · Admin" };

export default function AdminAnalyticsPage() {
  return <AnalyticsSection />;
}
