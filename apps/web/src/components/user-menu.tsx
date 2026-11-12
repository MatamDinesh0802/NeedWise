"use client";

import { LoaderCircle, LogIn, LogOut, Users } from "lucide-react";
import { usePathname } from "next/navigation";
import { Suspense, type MouseEvent } from "react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-api-query";
import { useSession } from "@/hooks/use-session";
import { DEMO_ACCOUNTS, fetchRuntimeConfig } from "@/lib/demo";
import {
  DEPARTMENT_ROLE_LABELS,
  initials,
  loginHref,
  ORG_ROLE_LABELS,
  type Me,
} from "@/lib/session";

function SignInAnchor({ href, title }: { href: string; title?: string }) {
  // Sign-in is a full navigation through the API to the identity provider.
  // On click we use the live location, so return_to always matches what's on screen.
  function withCurrentLocation(event: MouseEvent<HTMLAnchorElement>) {
    event.currentTarget.href = loginHref(window.location.pathname, window.location.search);
  }

  return (
    <a href={href} onClick={withCurrentLocation} title={title} className={buttonVariants({ size: "sm" })}>
      <LogIn data-icon="inline-start" />
      Sign in
    </a>
  );
}

function PathAwareSignInAnchor({ title }: { title?: string }) {
  return <SignInAnchor href={loginHref(usePathname())} title={title} />;
}

/**
 * The pathname is runtime data on dynamic routes, so it sits behind Suspense;
 * the fallback (prerendered shell) links to "/" until the real path streams in.
 */
export function SignInLink({ title }: { title?: string }) {
  return (
    <Suspense fallback={<SignInAnchor href={loginHref("/")} title={title} />}>
      <PathAwareSignInAnchor title={title} />
    </Suspense>
  );
}

function MenuDetails({ me }: { me: Me }) {
  return (
    <>
      <DropdownMenuGroup>
        <DropdownMenuLabel className="flex items-center gap-2.5 py-2 text-sm font-normal text-foreground">
          <Avatar size="lg">
            <AvatarFallback>{initials(me.display_name, me.email)}</AvatarFallback>
          </Avatar>
          <span className="grid min-w-0 leading-tight">
            <span className="truncate font-medium">{me.display_name}</span>
            <span className="truncate text-xs text-muted-foreground">{me.email}</span>
            {me.title ? (
              <span className="truncate text-xs text-muted-foreground">{me.title}</span>
            ) : null}
          </span>
        </DropdownMenuLabel>
      </DropdownMenuGroup>
      <DropdownMenuSeparator />
      <DropdownMenuGroup>
        <DropdownMenuLabel>Organization</DropdownMenuLabel>
        <div className="flex items-center justify-between gap-2 px-1.5 pb-1.5 text-sm">
          <span className="truncate">{me.tenant.name}</span>
          <Badge variant="secondary">{ORG_ROLE_LABELS[me.org_role]}</Badge>
        </div>
      </DropdownMenuGroup>
      <DropdownMenuGroup>
        <DropdownMenuLabel>{me.departments.length === 1 ? "Department" : "Departments"}</DropdownMenuLabel>
        {me.departments.length === 0 ? (
          <p className="px-1.5 pb-1.5 text-sm text-muted-foreground">None</p>
        ) : (
          <ul className="flex flex-col gap-1 px-1.5 pb-1.5">
            {me.departments.map((dept) => (
              <li key={dept.id} className="flex items-center justify-between gap-2 text-sm">
                <span className="truncate">{dept.name}</span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {DEPARTMENT_ROLE_LABELS[dept.role]}
                </span>
              </li>
            ))}
          </ul>
        )}
      </DropdownMenuGroup>
    </>
  );
}

/** Header sign-in control: skeleton while loading, a link when signed out, a menu when signed in. */
export function UserMenu() {
  const { state, signOut, switchAccount } = useSession();
  const config = useApiQuery("web/runtime-config", fetchRuntimeConfig);
  const demo = config.state.status === "success" && config.state.data.demo;

  if (state.kind === "loading") {
    return (
      <div className="flex items-center gap-2" aria-busy="true" aria-label="Loading your session">
        <Skeleton className="size-8 rounded-full" />
        <Skeleton className="hidden h-4 w-20 sm:block" />
      </div>
    );
  }

  if (state.kind === "signed_out") return <SignInLink />;
  if (state.kind === "error") return <SignInLink title={`Session check failed: ${state.message}`} />;

  const { me, signingOut, signOutError } = state;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" className="h-9 gap-2 px-1.5" aria-label={`Account menu for ${me.display_name}`} />
        }
      >
        <Avatar>
          <AvatarFallback className="text-xs">{initials(me.display_name, me.email)}</AvatarFallback>
        </Avatar>
        <span className="hidden max-w-40 truncate text-sm font-medium sm:inline">{me.display_name}</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <MenuDetails me={me} />
        <DropdownMenuSeparator />
        {demo ? (
          <>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <Users />
                View as…
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-64">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>Demo accounts · password workmind-demo</DropdownMenuLabel>
                  {DEMO_ACCOUNTS.map((account) => {
                    const current = account.email === me.email;
                    return (
                      <DropdownMenuItem
                        key={account.email}
                        disabled={current || signingOut}
                        onClick={() => void switchAccount(account.email)}
                      >
                        <span className="flex min-w-0 flex-1 flex-col">
                          <span className="truncate">
                            {account.name}
                            {current ? " (you)" : ""}
                          </span>
                          <span className="truncate text-xs text-muted-foreground">{account.role}</span>
                        </span>
                      </DropdownMenuItem>
                    );
                  })}
                </DropdownMenuGroup>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
          </>
        ) : null}
        {signOutError ? (
          <p role="alert" className="px-1.5 py-1 text-xs text-destructive">
            Sign out failed: {signOutError}
          </p>
        ) : null}
        <DropdownMenuItem
          disabled={signingOut}
          closeOnClick={false}
          onClick={() => void signOut()}
        >
          {signingOut ? <LoaderCircle className="animate-spin" /> : <LogOut />}
          {signingOut ? "Signing out…" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
