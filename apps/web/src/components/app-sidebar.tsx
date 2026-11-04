"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

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
} from "@/components/ui/sidebar";
import { activeNavItem, siteConfig, visibleNavItems, type NavItem } from "@/config/site";
import { useSession } from "@/hooks/use-session";

function NavMenu({ items, activeHref }: { items: NavItem[]; activeHref?: string }) {
  return (
    <SidebarMenu>
      {items.map((item) => {
        const active = item.href === activeHref;
        return (
          <SidebarMenuItem key={item.href}>
            <SidebarMenuButton
              isActive={active}
              tooltip={item.title}
              render={<Link href={item.href} aria-current={active ? "page" : undefined} />}
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

export function AppSidebar() {
  const pathname = usePathname();
  const activeHref = activeNavItem(pathname)?.href;
  const { state } = useSession();
  const role = state.kind === "signed_in" ? state.me.org_role : undefined;
  // Hiding Admin for non-admins is cosmetic only: the API enforces every permission.
  const adminItems = visibleNavItems("administration", role);

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" render={<Link href="/" />}>
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
