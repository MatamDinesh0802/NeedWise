"use client";

import { Building2, Ellipsis, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EMPTY_NAME_SLUG, NameSlugFields, nameSlugErrors, type NameSlugValue } from "@/components/admin/name-slug-fields";
import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { Field } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { adminApi, adminKeys, type Department } from "@/lib/admin/api";
import { errorMessage, fieldErrors } from "@/lib/error-messages";

export function DepartmentsSection() {
  const { state, refetch } = useApiQuery(adminKeys.departments, adminApi.listDepartments);
  const [creating, setCreating] = useState(false);
  const [renameTarget, setRenameTarget] = useState<Department | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Department | null>(null);

  return (
    <section className="flex flex-col gap-4" aria-labelledby="departments-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="departments-heading" className="flex items-center gap-2 text-lg font-semibold">
            Departments
            <RefreshingHint active={state.status !== "loading" && state.refreshing} />
          </h3>
          <p className="text-sm text-muted-foreground">Department members see their department&apos;s knowledge.</p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus data-icon="inline-start" />
          New department
        </Button>
      </div>

      {state.status === "loading" ? (
        <TableSkeleton />
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={() => void refetch()} />
      ) : state.data.items.length === 0 ? (
        <EmptyState icon={Building2} title="No departments yet" description="Create departments to organize people and knowledge." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead className="text-right">Members</TableHead>
              <TableHead>Managers</TableHead>
              <TableHead className="w-10">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.data.items.map((dept) => (
              <TableRow key={dept.id}>
                <TableCell className="font-medium">
                  <Link href={`/admin/departments/${dept.id}`} className="hover:underline">
                    {dept.name}
                  </Link>
                </TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">{dept.slug}</TableCell>
                <TableCell className="text-right tabular-nums">{dept.member_count}</TableCell>
                <TableCell>
                  {dept.managers.length ? (
                    dept.managers.map((m) => m.display_name).join(", ")
                  ) : (
                    <span className="text-muted-foreground">No manager</span>
                  )}
                </TableCell>
                <TableCell>
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${dept.name}`} />}
                    >
                      <Ellipsis />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-40">
                      <DropdownMenuItem render={<Link href={`/admin/departments/${dept.id}`} />}>Members</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => setRenameTarget(dept)}>Rename…</DropdownMenuItem>
                      <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(dept)}>
                        Delete…
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="sm:max-w-md">
          {creating ? <CreateDepartmentForm onDone={() => setCreating(false)} /> : null}
        </DialogContent>
      </Dialog>

      <Dialog open={renameTarget !== null} onOpenChange={(open) => !open && setRenameTarget(null)}>
        <DialogContent className="sm:max-w-md">
          {renameTarget ? (
            <RenameDepartmentForm key={renameTarget.id} department={renameTarget} onDone={() => setRenameTarget(null)} />
          ) : null}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={`Delete ${deleteTarget?.name ?? "department"}?`}
        description={`Its ${deleteTarget?.member_count ?? 0} memberships are removed and members lose access to knowledge shared only with this department. This can't be undone.`}
        confirmLabel="Delete department"
        onConfirm={async () => {
          if (!deleteTarget) return;
          await adminApi.deleteDepartment(deleteTarget.id);
          await Promise.all([invalidateQueries(adminKeys.departments), invalidateQueries(adminKeys.users)]);
        }}
      />
    </section>
  );
}

function CreateDepartmentForm({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState<NameSlugValue>(EMPTY_NAME_SLUG);
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const code = errorMessage(error).code;
  const serverSlugError =
    code === "slug_taken"
      ? "This slug is already in use."
      : (fieldErrors(error).find((f) => f.field === "slug")?.message ?? null);

  async function submit() {
    setShowErrors(true);
    const errors = nameSlugErrors(value);
    if (errors.name || errors.slug) return;
    setPending(true);
    setError(null);
    try {
      await adminApi.createDepartment({ name: value.name.trim(), slug: value.slug });
      await invalidateQueries(adminKeys.departments);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <DialogHeader>
        <DialogTitle>New department</DialogTitle>
        <DialogDescription>Add members after creating it.</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-3">
        <NameSlugFields
          value={value}
          onChange={(next) => {
            setValue(next);
            if (code === "slug_taken" && next.slug !== value.slug) setError(null);
          }}
          serverSlugError={serverSlugError}
          showErrors={showErrors}
        />
      </div>
      {error && !serverSlugError ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Creating…" : "Create department"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function RenameDepartmentForm({ department, onDone }: { department: Department; onDone: () => void }) {
  const [name, setName] = useState(department.name);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const trimmed = name.trim();

  async function submit() {
    setPending(true);
    setError(null);
    try {
      await adminApi.renameDepartment(department.id, trimmed);
      await Promise.all([invalidateQueries(adminKeys.departments), invalidateQueries(adminKeys.users)]);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="contents"
      onSubmit={(e) => {
        e.preventDefault();
        if (trimmed) void submit();
      }}
    >
      <DialogHeader>
        <DialogTitle>Rename department</DialogTitle>
        <DialogDescription>
          The slug <span className="font-mono">{department.slug}</span> stays the same.
        </DialogDescription>
      </DialogHeader>
      <Field label="Name" error={trimmed ? null : "Enter a name."}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            maxLength={200}
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
          />
        )}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !trimmed || trimmed === department.name}>
          {pending ? "Saving…" : "Rename"}
        </Button>
      </DialogFooter>
    </form>
  );
}
