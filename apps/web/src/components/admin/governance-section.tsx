"use client";

import Link from "next/link";

import { GovernanceOverviewSection } from "@/components/knowledge/governance-overview";

/** Admin > Governance: what needs attention in every collection (docs/governance.md). */
export function GovernanceSection() {
  return (
    <section className="flex flex-col gap-4" aria-labelledby="governance-heading">
      <div>
        <h3 id="governance-heading" className="text-lg font-semibold">
          Knowledge governance
        </h3>
        <p className="text-sm text-muted-foreground">
          Reviews that are overdue or due soon, documents nobody owns, requests for official status, and recent demotions.
        </p>
      </div>
      <GovernanceOverviewSection
        footer={
          <p className="text-sm text-muted-foreground">
            Collection owners and review intervals are set in{" "}
            <Link href="/admin/collections" className="text-primary hover:underline">
              Admin &gt; Collections
            </Link>
            . Every change to owners, reviews and official status is in the{" "}
            <Link href="/admin/audit?action=knowledge.document." className="text-primary hover:underline">
              audit log
            </Link>
            .
          </p>
        }
      />
    </section>
  );
}
