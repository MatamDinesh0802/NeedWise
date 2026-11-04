import { sectionMetadata } from "@/components/page-placeholder";
import { SettingsView } from "@/components/settings/settings-view";

export const metadata = sectionMetadata("/settings");

export default function SettingsPage() {
  return <SettingsView />;
}
