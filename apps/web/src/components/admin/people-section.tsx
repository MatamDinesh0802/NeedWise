"use client";

import { Ellipsis, Search, UserPlus, Users } from "lucide-react";
import { useMemo, useState } from "react";

import { RoleBadge, StatusBadge } from "@/components/admin/badges";
import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, TableSkeleton } from "@/components/data/states";
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
import { invalidateQueries } from "@/hooks/use-api-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { useSession } from "@/hooks/use-session";
import { useStore } from "@/hooks/use-store";
import { adminApi, adminKeys, type AdminUser } from "@/lib/admin/api";
import { roleOptions, rowActions, STATUS_FILTERS, toggledStatus, type StatusFilter } from "@/lib/admin/people";
import { errorMessage, fieldErrors } from "@/lib/error-messages";
import { formatRelative } from "@/lib/format";
import type { Me, OrgRole } from "@/lib/session";
import { createPagedList, type PagedList } from "@/lib/stores/paged-list";

const PAGE_SIZE = 25;

export function PeopleSection() {
  const { state } = useSession();
  if (state.kind !== "signed_in") return null; // AdminShell handles the other states
  return <People me={state.me} />;
}

function People({ me }: { me: Me }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("");
  const q = useDebouncedValue(query.trim(), 300);

  // A fresh list per filter; the first subscription loads page one.
  const list = useMemo(
    () =>
      createPagedList<AdminUser, string>(async (cursor) => {
        const page = await adminApi.listUsers({ q, status, limit: PAGE_SIZE, cursor });
        return { items: page.items, next: page.next_cursor };
      }),
    [q, status],
  );

  const [adding, setAdding] = useState(false);

  return (
    <section className="flex flex-col gap-4" aria-labelledby="people-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="people-heading" className="text-lg font-semibold">
            People
          </h3>
          <p className="text-sm text-muted-foreground">Everyone in your organization and what they can do.</p>
        </div>
        <Button onClick={() => setAdding(true)}>
          <UserPlus data-icon="inline-start" />
          Add person
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search name or email"
            aria-label="Search people"
            className="pl-8"
          />
        </div>
        <SimpleSelect
          value={status}
          onChange={setStatus}
          options={STATUS_FILTERS}
          ariaLabel="Filter by status"
        />
      </div>

      <PeopleTable list={list} me={me} filtered={Boolean(q || status)} />

      <AddPersonDialog
        open={adding}
        onOpenChange={setAdding}
        currentRole={me.org_role}
        onCreated={(user) => {
          list.prepend(user);
          void invalidateQueries(adminKeys.users);
        }}
      />
    </section>
  );
}

function PeopleTable({ list, me, filtered }: { list: PagedList<AdminUser>; me: Me; filtered: boolean }) {
  const state = useStore(list);
  const [roleTarget, setRoleTarget] = useState<AdminUser | null>(null);
  const [statusTarget, setStatusTarget] = useState<AdminUser | null>(null);

  if (state.status === "loading") return <TableSkeleton rows={5} />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void list.reload()} />;
  if (state.items.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title={filtered ? "No one matches" : "No people yet"}
        description={filtered ? "Try a different search or status filter." : "Add the first person to your organization."}
      />
    );
  }

  const applyUpdate = (updated: AdminUser) => {
    list.updateItem((u) => u.id === updated.id, updated);
    void invalidateQueries(adminKeys.users);
  };

  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Email</TableHead>
            <TableHead>Title</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Departments</TableHead>
            <TableHead>Last sign-in</TableHead>
            <TableHead className="w-10">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {state.items.map((user) => {
            const actions = rowActions(me, user);
            return (
              <TableRow key={user.id}>
                <TableCell className="font-medium">
                  {user.display_name}
                  {user.id === me.id ? <span className="ml-1.5 text-xs text-muted-foreground">(you)</span> : null}
                </TableCell>
                <TableCell className="text-muted-foreground">{user.email}</TableCell>
                <TableCell>{user.title ?? <span className="text-muted-foreground">—</span>}</TableCell>
                <TableCell>
                  <RoleBadge role={user.org_role} />
                </TableCell>
                <TableCell>
                  <StatusBadge status={user.status} />
                </TableCell>
                <TableCell className="max-w-48 truncate" title={user.departments.map((d) => d.name).join(", ")}>
                  {user.departments.length ? user.departments.map((d) => d.name).join(", ") : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {formatRelative(user.last_login_at)}
                </TableCell>
                <TableCell>
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Actions for ${user.display_name}`}
                          title={actions.reason ?? undefined}
                          disabled={!actions.canChangeRole && !actions.canToggleStatus}
                        />
                      }
                    >
                      <Ellipsis />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-44">
                      <DropdownMenuItem disabled={!actions.canChangeRole} onClick={() => setRoleTarget(user)}>
                        Change role…
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={!actions.canToggleStatus}
                        variant={user.status === "disabled" ? "default" : "destructive"}
                        onClick={() => setStatusTarget(user)}
                      >
                        {user.status === "disabled" ? "Enable…" : "Disable…"}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      {state.moreError ? <ErrorAlert error={state.moreError} onRetry={() => void list.loadMore()} /> : null}
      {state.hasMore ? (
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => void list.loadMore()} disabled={state.loadingMore}>
            {state.loadingMore ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}

      <ChangeRoleDialog
        user={roleTarget}
        currentRole={me.org_role}
        onClose={() => setRoleTarget(null)}
        onUpdated={applyUpdate}
      />
      <ConfirmDialog
        open={statusTarget !== null}
        onOpenChange={(open) => !open && setStatusTarget(null)}
        destructive={statusTarget?.status !== "disabled"}
        title={statusTarget?.status === "disabled" ? `Enable ${statusTarget.display_name}?` : `Disable ${statusTarget?.display_name ?? ""}?`}
        description={
          statusTarget?.status === "disabled"
            ? "They will be able to sign in again with their existing role and memberships."
            : "They will be signed out everywhere right away and can't sign in until enabled again. Their memberships stay."
        }
        confirmLabel={statusTarget?.status === "disabled" ? "Enable" : "Disable"}
        onConfirm={async () => {
          if (!statusTarget) return;
          applyUpdate(await adminApi.updateUser(statusTarget.id, { status: toggledStatus(statusTarget.status) }));
        }}
      />
    </>
  );
}

function ChangeRoleDialog({
  user,
  currentRole,
  onClose,
  onUpdated,
}: {
  user: AdminUser | null;
  currentRole: OrgRole;
  onClose: () => void;
  onUpdated: (user: AdminUser) => void;
}) {
  return (
    <Dialog open={user !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        {user ? (
          // Keyed so the form resets for each person.
          <ChangeRoleForm key={user.id} user={user} currentRole={currentRole} onClose={onClose} onUpdated={onUpdated} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function ChangeRoleForm({
  user,
  currentRole,
  onClose,
  onUpdated,
}: {
  user: AdminUser;
  currentRole: OrgRole;
  onClose: () => void;
  onUpdated: (user: AdminUser) => void;
}) {
  const [role, setRole] = useState<OrgRole>(user.org_role);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function save() {
    setPending(true);
    setError(null);
    try {
      onUpdated(await adminApi.updateUser(user.id, { org_role: role }));
      onClose();
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
        void save();
      }}
    >
      <DialogHeader>
        <DialogTitle>Change role</DialogTitle>
        <DialogDescription>
          {user.display_name} ({user.email})
        </DialogDescription>
      </DialogHeader>
      <Field label="Organization role" hint="Owners and admins manage the organization; auditors can read the audit log.">
        {({ id }) => <SimpleSelect id={id} value={role} onChange={setRole} options={roleOptions(currentRole)} />}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || role === user.org_role}>
          {pending ? "Saving…" : "Save role"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function AddPersonDialog({
  open,
  onOpenChange,
  currentRole,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentRole: OrgRole;
  onCreated: (user: AdminUser) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        {open ? <AddPersonForm currentRole={currentRole} onCancel={() => onOpenChange(false)} onCreated={(u) => {
          onCreated(u);
          onOpenChange(false);
        }} /> : null}
      </DialogContent>
    </Dialog>
  );
}

function AddPersonForm({
  currentRole,
  onCancel,
  onCreated,
}: {
  currentRole: OrgRole;
  onCancel: () => void;
  onCreated: (user: AdminUser) => void;
}) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [role, setRole] = useState<OrgRole>("member");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const fields = Object.fromEntries(fieldErrors(error).map((f) => [f.field, f.message]));
  const emailError = fields.email ?? (errorMessage(error).code === "email_taken" ? "This email is already in use." : null);

  async function submit() {
    setPending(true);
    setError(null);
    try {
      onCreated(
        await adminApi.createUser({
          email: email.trim(),
          display_name: name.trim(),
          title: title.trim() || null,
          org_role: role,
        }),
      );
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
        void submit();
      }}
    >
      <DialogHeader>
        <DialogTitle>Add person</DialogTitle>
        <DialogDescription>They appear as Invited until they sign in for the first time.</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-3">
        <Field label="Email" error={emailError}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="email"
              required
              autoComplete="off"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>
        <Field label="Name" error={fields.display_name}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              required
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>
        <Field label="Job title (optional)" error={fields.title}>
          {({ id, describedBy }) => (
            <Input id={id} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} aria-describedby={describedBy} />
          )}
        </Field>
        <Field label="Organization role">
          {({ id }) => <SimpleSelect id={id} value={role} onChange={setRole} options={roleOptions(currentRole)} />}
        </Field>
      </div>
      {error && !emailError && Object.keys(fields).length === 0 ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !email.trim() || !name.trim()}>
          {pending ? "Adding…" : "Add person"}
        </Button>
      </DialogFooter>
    </form>
  );
}
