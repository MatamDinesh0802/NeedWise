import { PagePlaceholder, sectionMetadata } from "@/components/page-placeholder";

export const metadata = sectionMetadata("/admin");

export default function AdminPage() {
  return <PagePlaceholder href="/admin" />;
}
