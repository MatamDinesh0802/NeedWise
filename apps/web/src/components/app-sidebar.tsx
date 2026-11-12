"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { BrandMark } from "@/components/brand-mark";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { activeNavItem, siteConfig, visibleNavItems, type NavItem } from "@/config/site";
import { useSession } from "@/hooks/use-session";

/**
 * On a phone the sidebar is a sheet over the page: close it once a link is chosen, or it keeps
 * covering the page that link opened. On wider screens it stays as it is.
 */
function useCloseOnNavigate(): () => void {
  const { isMobile, setOpenMobile } = useSidebar();
  return () => {
    if (isMobile) setOpenMobile(false);
  };
}

function NavMenu({ items, activeHref }: { items: NavItem[]; activeHref?: string }) {
  const closeOnNavigate = useCloseOnNavigate();
  return (
    <SidebarMenu>
      {items.map((item) => {
        const active = item.href === activeHref;
        return (
          <SidebarMenuItem key={item.href}>
            <SidebarMenuButton
              isActive={active}
              tooltip={item.title}
              render={<Link href={item.href} aria-current={active ? "page" : undefined} onClick={closeOnNavigate} />}
            >
              <item.icon />
              <span>{item.title}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}

function PathAwareSidebar() {
  return <SidebarView activeHref={activeNavItem(usePathname())?.href} />;
}

/** usePathname is runtime data on dynamic routes; the prerendered shell has no active item. */
export function AppSidebar() {
  return (
    <Suspense fallback={<SidebarView activeHref={undefined} />}>
      <PathAwareSidebar />
    </Suspense>
  );
}

function SidebarView({ activeHref }: { activeHref: string | undefined }) {
  const { state } = useSession();
  const role = state.kind === "signed_in" ? state.me.org_role : undefined;
  // Hiding Admin for non-admins is cosmetic only: the API enforces every permission.
  const adminItems = visibleNavItems("administration", role);
  const closeOnNavigate = useCloseOnNavigate();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" render={<Link href="/" onClick={closeOnNavigate} />}>
              <BrandMark className="size-8!" />
              <div className="grid flex-1 text-left leading-tight">
                <span className="truncate font-semibold">{siteConfig.name}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {siteConfig.shortTagline}
                </span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Workspace</SidebarGroupLabel>
          <SidebarGroupContent>
            <NavMenu items={visibleNavItems("workspace", role)} activeHref={activeHref} />
          </SidebarGroupContent>
        </SidebarGroup>

        <div className="mt-auto">
          {adminItems.length > 0 ? (
            <SidebarGroup>
              <SidebarGroupLabel>Administration</SidebarGroupLabel>
              <SidebarGroupContent>
                <NavMenu items={adminItems} activeHref={activeHref} />
              </SidebarGroupContent>
            </SidebarGroup>
          ) : null}
          <SidebarGroup>
            <SidebarGroupLabel>Account</SidebarGroupLabel>
            <SidebarGroupContent>
              <NavMenu items={visibleNavItems("account", role)} activeHref={activeHref} />
            </SidebarGroupContent>
          </SidebarGroup>
        </div>
      </SidebarContent>

      <SidebarFooter>
        <p className="px-2 pb-1 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
          Open source · Apache-2.0 · {siteConfig.stage}
        </p>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
