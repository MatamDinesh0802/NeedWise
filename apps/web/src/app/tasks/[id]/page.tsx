import type { Metadata } from "next";
import { Suspense } from "react";

import { TableSkeleton } from "@/components/data/states";
import { TaskRoute } from "@/components/work/tasks-view";

export const metadata: Metadata = { title: "Task · Tasks" };

// Notification links point here (/tasks/{id}); the id is runtime data, read behind Suspense.
export default function TaskPage() {
  return (
    <Suspense fallback={<TableSkeleton rows={3} />}>
      <TaskRoute />
    </Suspense>
  );
}
