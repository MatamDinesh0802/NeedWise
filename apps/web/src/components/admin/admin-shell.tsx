"use client";

import { ShieldOff } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { EmptyState } from "@/components/data/states";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { adminSectionForPath, canAccessSection, visibleAdminSections } from "@/lib/admin/sections";
import { cn } from "@/lib/utils";

/**
 * Frame for every /admin page: access gate plus the secondary navigation.
 * Hiding sections is a convenience; the API enforces every permission (403 "forbidden").
 */
export function AdminShell({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      {/* usePathname is runtime data on dynamic routes (e.g. /admin/groups/[id]). */}
      <Suspense fallback={<AdminShellSkeleton />}>
        <PathAwareAdminShell>{children}</PathAwareAdminShell>
      </Suspense>
    </div>
  );
}

function AdminShellSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading administration">
      <Skeleton className="h-7 w-40" />
      <Skeleton className="h-8 w-80" />
      <Skeleton className="h-48 w-full" />
    </div>
  );
}

function PathAwareAdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <SignedInGate purpose="manage your organization">
      {(me) => {
        const sections = visibleAdminSections(me.org_role);
        const current = adminSectionForPath(pathname);

        if (sections.length === 0) {
          return (
            <EmptyState
              icon={ShieldOff}
              title="You don't have access to administration"
              description="Only owners, admins and auditors can open these pages. Ask an administrator if you need access."
            />
          );
        }

        return (
          <>
            <header className="flex flex-col gap-1">
              <h2 className="text-xl font-semibold tracking-tight">Administration</h2>
              <p className="text-sm text-muted-foreground">{me.tenant.name}</p>
            </header>

            <nav aria-label="Administration sections" className="flex flex-wrap gap-1 border-b pb-2">
              {sections.map((section) => {
                const active = current?.key === section.key;
                return (
                  <Link
                    key={section.key}
                    href={section.href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      buttonVariants({ variant: active ? "secondary" : "ghost", size: "sm" }),
                      active && "font-semibold",
                    )}
                  >
                    {section.title}
                  </Link>
                );
              })}
            </nav>

            {current && !canAccessSection(current, me.org_role) ? (
              <EmptyState
                icon={ShieldOff}
                title={`You don't have access to ${current.title}`}
                description="Your organization role doesn't include this section."
                action={
                  <Link href={sections[0].href} className={buttonVariants({ variant: "outline", size: "sm" })}>
                    Go to {sections[0].title}
                  </Link>
                }
              />
            ) : (
              children
            )}
          </>
        );
      }}
    </SignedInGate>
  );
}
