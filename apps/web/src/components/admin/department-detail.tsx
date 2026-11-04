"use client";

import { ArrowLeft, SearchX, UserMinus, Users } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { PersonPicker } from "@/components/admin/person-picker";
import { SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { adminApi, adminKeys, type Department, type DepartmentMember } from "@/lib/admin/api";
import { DEPARTMENT_ROLE_LABELS, type DepartmentRole } from "@/lib/session";

const ROLE_OPTIONS = (["member", "manager"] as const).map((value) => ({ value, label: DEPARTMENT_ROLE_LABELS[value] }));

/** Refresh everything a membership change affects: members, counts/managers, people rows. */
function refreshAfterMembershipChange(departmentId: string) {
  return Promise.all([
    invalidateQueries(adminKeys.departmentMembers(departmentId)),
    invalidateQueries(adminKeys.departments),
    invalidateQueries(adminKeys.users),
  ]);
}

export function DepartmentDetail() {
  const { id } = useParams<{ id: string }>();
  const departments = useApiQuery(adminKeys.departments, adminApi.listDepartments);

  const back = (
    <Link href="/admin/departments" className={buttonVariants({ variant: "ghost", size: "sm", className: "w-fit -ml-2" })}>
      <ArrowLeft data-icon="inline-start" />
      Departments
    </Link>
  );

  if (departments.state.status === "loading") {
    return (
      <div className="flex flex-col gap-4">
        {back}
        <Skeleton className="h-8 w-64" />
        <TableSkeleton />
      </div>
    );
  }
  if (departments.state.status === "error") {
    return (
      <div className="flex flex-col gap-4">
        {back}
        <ErrorAlert error={departments.state.error} onRetry={() => void departments.refetch()} />
      </div>
    );
  }
  const department = departments.state.data.items.find((d) => d.id === id);
  if (!department) {
    return (
      <div className="flex flex-col gap-4">
        {back}
        <EmptyState icon={SearchX} title="Department not found" description="It may have been deleted." />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {back}
      <div>
        <h3 className="text-lg font-semibold">{department.name}</h3>
        <p className="font-mono text-xs text-muted-foreground">{department.slug}</p>
      </div>
      <Members department={department} />
    </div>
  );
}

function Members({ department }: { department: Department }) {
  const { state, refetch } = useApiQuery(adminKeys.departmentMembers(department.id), () =>
    adminApi.listDepartmentMembers(department.id),
  );
  const [addRole, setAddRole] = useState<DepartmentRole>("member");
  const [busy, setBusy] = useState<string | null>(null); // user id being changed, or "add"
  const [error, setError] = useState<unknown>(null);

  async function run(key: string, action: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await action();
      await refreshAfterMembershipChange(department.id);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  const members: DepartmentMember[] = state.status === "success" ? state.data.items : [];
  const memberIds = new Set(members.map((m) => m.user_id));

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <h4 className="font-medium">Members</h4>
          <RefreshingHint active={state.status !== "loading" && state.refreshing} />
        </div>
        {error ? <ErrorAlert error={error} /> : null}
        {state.status === "loading" ? (
          <TableSkeleton rows={3} />
        ) : state.status === "error" ? (
          <ErrorAlert error={state.error} onRetry={() => void refetch()} />
        ) : members.length === 0 ? (
          <EmptyState icon={Users} title="No members yet" description="Add people with the form on the right." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead className="w-10">
                  <span className="sr-only">Remove</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.map((member) => (
                <TableRow key={member.user_id}>
                  <TableCell className="font-medium">{member.display_name}</TableCell>
                  <TableCell className="text-muted-foreground">{member.email}</TableCell>
                  <TableCell>
                    <SimpleSelect
                      size="sm"
                      value={member.role}
                      options={ROLE_OPTIONS}
                      disabled={busy !== null}
                      ariaLabel={`Role of ${member.display_name}`}
                      onChange={(role) =>
                        void run(member.user_id, () => adminApi.setDepartmentMember(department.id, member.user_id, role))
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      disabled={busy !== null}
                      aria-label={`Remove ${member.display_name} from ${department.name}`}
                      title="Remove from department"
                      onClick={() =>
                        void run(member.user_id, () => adminApi.removeDepartmentMember(department.id, member.user_id))
                      }
                    >
                      <UserMinus />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      <Card size="sm" className="h-fit">
        <CardHeader>
          <CardTitle>Add member</CardTitle>
          <CardDescription>Search for a person, choose their role, then pick them.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <SimpleSelect value={addRole} onChange={setAddRole} options={ROLE_OPTIONS} ariaLabel="Role for the new member" />
          <PersonPicker
            exclude={memberIds}
            disabled={busy !== null}
            onPick={(user) => void run("add", () => adminApi.setDepartmentMember(department.id, user.id, addRole))}
          />
        </CardContent>
      </Card>
    </div>
  );
}
