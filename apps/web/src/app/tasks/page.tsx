import { sectionMetadata } from "@/components/page-placeholder";
import { TasksView } from "@/components/work/tasks-view";

export const metadata = sectionMetadata("/tasks");

export default function TasksPage() {
  return <TasksView />;
}
