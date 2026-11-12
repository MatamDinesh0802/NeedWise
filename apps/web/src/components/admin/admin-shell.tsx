"use client";

import { LockKeyhole, ShieldOff } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { EmptyState, ErrorAlert } from "@/components/data/states";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { SignInLink } from "@/components/user-menu";
import { useSession } from "@/hooks/use-session";
import { adminAccess, keepsPage, pageVerdict, type AdminAccess } from "@/lib/admin/access";
import { adminSectionForPath, type AdminSection } from "@/lib/admin/sections";
import { cn } from "@/lib/utils";

/**
 * Frame for every /admin page: access gate plus the secondary navigation.
 * Hiding sections is a convenience; the API enforces every permission (403 "forbidden").
 *
 * The page is always rendered while we may still show it, hidden until the session says the
 * person may open this section (lib/admin/access.ts). Rendering it, rather than leaving it out
 * until the session loads, is what lets Next.js validate the page's instant navigation; it also
 * starts the page's own requests alongside the session's. Anyone who may not open it never sees
 * it, and it is removed as soon as that is known.
 */
export function AdminShell({ children }: { children: ReactNode }) {
  const { state, reload } = useSession();
  const access = adminAccess(state);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      {access.kind === "signed_out" ? (
        <EmptyState
          icon={LockKeyhole}
          title="Sign in to continue"
          description="You need to be signed in to manage your organization."
          action={<SignInLink />}
        />
      ) : access.kind === "error" ? (
        <ErrorAlert error={new Error(access.message)} onRetry={() => void reload()} />
      ) : access.kind === "no_admin" ? (
        <EmptyState
          icon={ShieldOff}
          title="You don't have access to administration"
          description="Only owners, admins and auditors can open these pages. Ask an administrator if you need access."
        />
      ) : (
        <>
          {access.kind === "admin" ? (
            <header className="flex flex-col gap-1">
              <h2 className="text-xl font-semibold tracking-tight">Administration</h2>
              <p className="text-sm text-muted-foreground">{access.me.tenant.name}</p>
            </header>
          ) : (
            <Skeleton className="h-7 w-40" aria-hidden="true" />
          )}
          {/* usePathname is runtime data on dynamic routes (e.g. /admin/groups/[id]). */}
          <Suspense fallback={<Skeleton className="h-8 w-80" aria-hidden="true" />}>
            <AdminNav sections={access.kind === "admin" ? access.sections : null} />
          </Suspense>
        </>
      )}
      {keepsPage(access) ? (
        // Where the URL can't be read yet (prerendering a dynamic route), the page still renders,
        // hidden: the fallback keeps it in the tree.
        <Suspense fallback={<PageSlot visible={false}>{children}</PageSlot>}>
          <SectionGate access={access}>{children}</SectionGate>
        </Suspense>
      ) : null}
    </div>
  );
}

function AdminNav({ sections }: { sections: AdminSection[] | null }) {
  const pathname = usePathname();
  if (sections === null) return <Skeleton className="h-8 w-80" aria-hidden="true" />;
  const current = pathname ? adminSectionForPath(pathname) : null;
  return (
    <nav aria-label="Administration sections" className="flex flex-wrap gap-1 border-b pb-2">
      {sections.map((section) => {
        const active = current?.key === section.key;
        return (
          <Link
            key={section.key}
            href={section.href}
            aria-current={active ? "page" : undefined}
            className={cn(buttonVariants({ variant: active ? "secondary" : "ghost", size: "sm" }), active && "font-semibold")}
          >
            {section.title}
          </Link>
        );
      })}
    </nav>
  );
}

function SectionGate({ access, children }: { access: AdminAccess; children: ReactNode }) {
  const verdict = pageVerdict(access, usePathname());
  if (verdict.kind === "denied") {
    return (
      <EmptyState
        icon={ShieldOff}
        title={`You don't have access to ${verdict.section.title}`}
        description="Your organization role doesn't include this section."
        action={
          <Link href={verdict.fallback.href} className={buttonVariants({ variant: "outline", size: "sm" })}>
            Go to {verdict.fallback.title}
          </Link>
        }
      />
    );
  }
  return <PageSlot visible={verdict.kind === "allowed"}>{children}</PageSlot>;
}

/** The page, hidden while access is being checked. The page keeps its place (and its state)
 * in the tree when it is shown. */
export function PageSlot({ visible, children }: { visible: boolean; children: ReactNode }) {
  return (
    <>
      {visible ? null : (
        <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading administration">
          <Skeleton className="h-48 w-full" />
        </div>
      )}
      <div hidden={!visible} className={visible ? "contents" : undefined}>
        {children}
      </div>
    </>
  );
}
