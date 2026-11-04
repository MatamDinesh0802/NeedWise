import { Badge } from "@/components/ui/badge";
import type { UserStatus } from "@/lib/admin/api";
import { ORG_ROLE_LABELS, type OrgRole } from "@/lib/session";

const ROLE_VARIANT: Record<OrgRole, "default" | "secondary" | "outline"> = {
  owner: "default",
  admin: "default",
  auditor: "secondary",
  member: "outline",
  guest: "outline",
};

export function RoleBadge({ role }: { role: OrgRole }) {
  return <Badge variant={ROLE_VARIANT[role] ?? "outline"}>{ORG_ROLE_LABELS[role] ?? role}</Badge>;
}

const STATUS: Record<UserStatus, { label: string; className: string }> = {
  active: { label: "Active", className: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
  invited: { label: "Invited", className: "bg-sky-500/10 text-sky-700 dark:text-sky-400" },
  disabled: { label: "Disabled", className: "bg-muted text-muted-foreground" },
};

export function StatusBadge({ status }: { status: UserStatus }) {
  const s = STATUS[status] ?? { label: status, className: "" };
  return (
    <Badge variant="secondary" className={s.className}>
      {s.label}
    </Badge>
  );
}
