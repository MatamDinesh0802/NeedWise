import { AdminIndex } from "@/components/admin/admin-index";
import { sectionMetadata } from "@/components/page-placeholder";

export const metadata = sectionMetadata("/admin");

export default function AdminPage() {
  return <AdminIndex />;
}
