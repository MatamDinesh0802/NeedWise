import type { Metadata } from "next";
import { Suspense } from "react";

import { TableSkeleton } from "@/components/data/states";
import { MeetingRoute } from "@/components/meetings/meeting-detail";

export const metadata: Metadata = { title: "Meeting · Meetings" };

// The id and ?t= are runtime data: read behind Suspense so the shell stays static.
export default function MeetingPage() {
  return (
    <Suspense fallback={<TableSkeleton rows={4} />}>
      <MeetingRoute />
    </Suspense>
  );
}
