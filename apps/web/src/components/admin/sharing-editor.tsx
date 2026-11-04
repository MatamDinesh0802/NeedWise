"use client";

import { Lock, X } from "lucide-react";
import { useState } from "react";

import { PersonPicker } from "@/components/admin/person-picker";
import { SimpleSelect } from "@/components/data/form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Department, Group } from "@/lib/admin/api";
import {
  principal,
  principalLabel,
  sortPrincipals,
  upsertEntry,
  type AccessLevel,
  type Directory,
  type PrincipalKind,
  type ShareEntry,
} from "@/lib/knowledge/principals";

const LEVELS: { value: AccessLevel; label: string }[] = [
  { value: "view", label: "Can view" },
  { value: "edit", label: "Can edit" },
];

const KINDS: { value: PrincipalKind; label: string }[] = [
  { value: "user", label: "Person" },
  { value: "group", label: "Group" },
  { value: "dept", label: "Department members" },
  { value: "dept-manager", label: "Department managers" },
  { value: "org", label: "Everyone in the organization" },
];

/**
 * Who can view / edit. `fixed` principals are added by the API anyway (scope defaults
 * on create) and are shown read-only.
 */
export function SharingEditor({
  entries,
  onChange,
  directory,
  groups,
  departments,
  fixed = [],
  disabled,
}: {
  entries: ShareEntry[];
  onChange: (entries: ShareEntry[]) => void;
  directory: Directory;
  groups: Group[];
  departments: Department[];
  fixed?: string[];
  disabled?: boolean;
}) {
  const levels = new Map(entries.map((e) => [e.principal, e.level]));
  const ordered = sortPrincipals([...new Set([...fixed, ...entries.map((e) => e.principal)])], directory);

  return (
    <div className="flex flex-col gap-3">
      {ordered.length === 0 ? (
        <p className="rounded-lg border border-dashed px-3 py-4 text-center text-sm text-muted-foreground">
          Nobody yet. Add people, groups or departments below.
        </p>
      ) : (
        <ul className="flex flex-col divide-y rounded-lg border" aria-label="Who has access">
          {ordered.map((p) => {
            const label = principalLabel(p, directory);
            const isFixed = fixed.includes(p) && !levels.has(p);
            return (
              <li key={p} className="flex items-center gap-2 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className={label.known ? "truncate text-sm font-medium" : "truncate text-sm text-muted-foreground italic"}>
                    {label.label}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {label.kindLabel}
                    {label.detail ? ` · ${label.detail}` : ""}
                  </p>
                </div>
                {isFixed ? (
                  <Badge variant="outline" title="Added automatically for this scope">
                    <Lock data-icon="inline-start" aria-hidden="true" />
                    Can view (scope)
                  </Badge>
                ) : (
                  <>
                    <SimpleSelect
                      size="sm"
                      value={levels.get(p) ?? "view"}
                      options={LEVELS}
                      disabled={disabled}
                      ariaLabel={`Access for ${label.label}`}
                      onChange={(level) => onChange(upsertEntry(entries, { principal: p, level }))}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      disabled={disabled}
                      aria-label={`Remove ${label.label}`}
                      onClick={() => onChange(entries.filter((e) => e.principal !== p))}
                    >
                      <X />
                    </Button>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <PrincipalAdder
        existing={new Set(ordered)}
        directory={directory}
        groups={groups}
        departments={departments}
        disabled={disabled}
        onAdd={(entry) => onChange(upsertEntry(entries, entry))}
      />
    </div>
  );
}

function PrincipalAdder({
  existing,
  directory,
  groups,
  departments,
  disabled,
  onAdd,
}: {
  existing: ReadonlySet<string>;
  directory: Directory;
  groups: Group[];
  departments: Department[];
  disabled?: boolean;
  onAdd: (entry: ShareEntry) => void;
}) {
  const [kind, setKind] = useState<PrincipalKind>("user");
  const [level, setLevel] = useState<AccessLevel>("view");
  const [target, setTarget] = useState("");

  const add = (id: string) => {
    onAdd({ principal: principal(kind, id), level });
    setTarget("");
  };

  const options =
    kind === "group"
      ? groups.map((g) => ({ value: g.id, label: g.name }))
      : kind === "dept" || kind === "dept-manager"
        ? departments.map((d) => ({ value: d.id, label: d.name }))
        : [];
  const available = options.filter((o) => !existing.has(principal(kind, o.value)));
  const orgPrincipal = principal("org", directory.tenant.id);

  return (
    <fieldset className="flex flex-col gap-2 rounded-lg border bg-muted/20 p-3" disabled={disabled}>
      <legend className="px-1 text-xs font-medium text-muted-foreground">Add access</legend>
      <div className="flex flex-wrap gap-2">
        <SimpleSelect
          value={kind}
          onChange={(next) => {
            setKind(next);
            setTarget("");
          }}
          options={KINDS}
          ariaLabel="Type of grantee"
          className="min-w-44"
        />
        <SimpleSelect value={level} onChange={setLevel} options={LEVELS} ariaLabel="Access level" />
      </div>

      {kind === "user" ? (
        <PersonPicker
          label="Find a person to share with"
          exclude={new Set([...existing].filter((p) => p.startsWith("user:")).map((p) => p.slice(5)))}
          onPick={(user) => add(user.id)}
        />
      ) : kind === "org" ? (
        <Button
          variant="outline"
          className="w-fit"
          disabled={existing.has(orgPrincipal)}
          onClick={() => add(directory.tenant.id)}
        >
          {existing.has(orgPrincipal) ? `Everyone at ${directory.tenant.name} already has access` : `Add everyone at ${directory.tenant.name}`}
        </Button>
      ) : available.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {options.length === 0 ? "None exist yet." : "All of them already have access."}
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <SimpleSelect
            value={target}
            onChange={setTarget}
            ariaLabel={kind === "group" ? "Group" : "Department"}
            className="min-w-56"
            options={[{ value: "", label: kind === "group" ? "Choose a group…" : "Choose a department…", disabled: true }, ...available]}
          />
          <Button variant="outline" disabled={!target} onClick={() => add(target)}>
            Add
          </Button>
        </div>
      )}
    </fieldset>
  );
}
