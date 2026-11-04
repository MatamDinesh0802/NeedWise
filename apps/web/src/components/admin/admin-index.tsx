"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useSession } from "@/hooks/use-session";
import { firstAdminSection, visibleAdminSections } from "@/lib/admin/sections";

/** /admin: forwards to the first section this user may open, and lists them meanwhile. */
export function AdminIndex() {
  const router = useRouter();
  const { state } = useSession();
  const role = state.kind === "signed_in" ? state.me.org_role : null;
  const first = firstAdminSection(role);

  useEffect(() => {
    if (first) router.replace(first.href);
  }, [first, router]);

  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {visibleAdminSections(role).map((section) => (
        <li key={section.key}>
          <Link href={section.href} className="block rounded-xl border p-4 transition-colors hover:bg-muted/50">
            <p className="font-medium">{section.title}</p>
            <p className="text-sm text-muted-foreground">{section.description}</p>
          </Link>
        </li>
      ))}
    </ul>
  );
}
