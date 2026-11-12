"use client";

import { Building2, Info, LoaderCircle, Plus, Trash2, Undo2, UserRound } from "lucide-react";
import Link from "next/link";
import { useId, useState, type ReactNode } from "react";

import { Field, SimpleSelect } from "@/components/data/form";
import { ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { EnterpriseBadge, EnterpriseGateView, EnterpriseNotice } from "@/components/enterprise/enterprise-gate";
import { BudgetStatusLabel } from "@/components/usage/budget-meter";
import { ColleaguePicker } from "@/components/work/colleague-picker";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useApiQuery } from "@/hooks/use-api-query";
import { useEdition } from "@/hooks/use-edition";
import { adminApi, adminKeys, type Department } from "@/lib/admin/api";
import { ApiError } from "@/lib/api";
import { hasFeature, isEnterpriseFeatureError, type EditionInfo } from "@/lib/edition";
import { getQueryCache } from "@/lib/stores/query-cache";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";
import { usageApi, usageKeys, type AdminBudget, type AdminBudgets, type BudgetPeriod, type BudgetScope } from "@/lib/usage/api";
import {
  ACTION_HINTS,
  ACTION_OPTIONS,
  formFromBudgets,
  isDirty,
  newRow,
  PERIOD_OPTIONS,
  problemsFromError,
  toUpdate,
  validateBudgets,
  type BudgetRow,
  type BudgetsForm,
  type DefaultCapForm,
  type FormErrors,
} from "@/lib/usage/budgets";
import { formatUsd, PERIOD_ADJECTIVES } from "@/lib/usage/format";

/** Admin › Budgets: spending limits on model usage. The API enforces them; this edits them. */
export function BudgetsSection() {
  const budgets = useApiQuery(usageKeys.budgets, usageApi.budgets);
  const { edition, loading: editionLoading } = useEdition();
  return (
    <section className="flex flex-col gap-4" aria-labelledby="budgets-heading">
      <div>
        <h3 id="budgets-heading" className="flex items-center gap-2 text-lg font-semibold">
          Budgets
          <RefreshingHint active={budgets.state.status === "success" && budgets.state.refreshing} />
        </h3>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Spending limits on model usage, in US dollars, per day or per month. A budget that blocks refuses requests that use a model
          once it is used up, until it resets; one that only warns lets them through. At its warning level, each budget notifies once
          per period: owners and admins for the organization, managers and admins for a department, and the person for their own cap.
        </p>
      </div>
      {budgets.state.status === "loading" ? (
        <TableSkeleton rows={3} />
      ) : budgets.state.status === "error" ? (
        <ErrorAlert error={budgets.state.error} onRetry={() => void budgets.refetch()} />
      ) : (
        <BudgetsEditor saved={budgets.state.data} edition={edition} editionLoading={editionLoading} />
      )}
      <p className="text-xs text-muted-foreground">
        See what has been spent on the{" "}
        <Link href="/analytics" className="underline underline-offset-4">
          Usage
        </Link>{" "}
        page.
      </p>
    </section>
  );
}

type SaveProblem = { kind: "enterprise" } | { kind: "invalid"; general: string[] } | { kind: "error"; error: unknown };

/** The form for one saved state. Exported for tests: no fetching of its own except people and departments. */
export function BudgetsEditor({
  saved: initial,
  edition,
  editionLoading = false,
}: {
  saved: AdminBudgets;
  edition: EditionInfo | null;
  editionLoading?: boolean;
}) {
  const [saved, setSaved] = useState(initial);
  const [form, setForm] = useState<BudgetsForm>(() => formFromBudgets(initial));
  const [attempted, setAttempted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [serverErrors, setServerErrors] = useState<FormErrors>({});
  const [problem, setProblem] = useState<SaveProblem | null>(null);

  const departmentsLicensed = hasFeature(edition, "department_budgets");
  // Unknown edition (still loading, or the request failed): send everything and let the API decide.
  const includeDepartments = departmentsLicensed || edition === null;
  const localErrors = validateBudgets(form);
  const errors: FormErrors = { ...(attempted ? localErrors : {}), ...serverErrors };
  const dirty = isDirty(form, saved);
  const status = new Map<string, AdminBudget>(saved.budgets.map((b) => [b.id, b]));
  const rows = (scope: BudgetScope) => form.rows.filter((row) => row.scope === scope);
  const departmentRows = rows("department");
  const droppedDepartments = !includeDepartments && departmentRows.length > 0;

  function update(next: BudgetsForm) {
    setForm(next);
    setServerErrors({});
  }
  const patchRow = (key: string, patch: Partial<BudgetRow>) =>
    update({ ...form, rows: form.rows.map((row) => (row.key === key ? { ...row, ...patch } : row)) });
  const removeRow = (key: string) => update({ ...form, rows: form.rows.filter((row) => row.key !== key) });
  const addRow = (row: BudgetRow) => update({ ...form, rows: [...form.rows, row] });
  const patchDefault = (patch: Partial<DefaultCapForm>) => update({ ...form, userDefault: { ...form.userDefault, ...patch } });

  async function save() {
    setAttempted(true);
    setProblem(null);
    if (Object.keys(localErrors).length > 0) {
      // Move focus to the first field that needs fixing.
      requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-budgets-form] [aria-invalid="true"]')?.focus());
      return;
    }
    const { body, order } = toUpdate(form, { includeDepartments });
    setSaving(true);
    try {
      const result = await usageApi.saveBudgets(body);
      getQueryCache().setData<AdminBudgets>(usageKeys.budgets, () => result);
      setSaved(result);
      setForm(formFromBudgets(result));
      setAttempted(false);
      setServerErrors({});
      toast({ title: "Budgets saved", description: "They apply to the next request." });
    } catch (error) {
      if (isEnterpriseFeatureError(error)) setProblem({ kind: "enterprise" });
      else if (error instanceof ApiError && error.status === 422) {
        const { fields, general } = problemsFromError(error, order);
        setServerErrors(fields);
        setProblem({ kind: "invalid", general });
      } else setProblem({ kind: "error", error });
    } finally {
      setSaving(false);
    }
  }

  const errorCount = Object.keys(errors).length;

  return (
    <div className="flex flex-col gap-5" data-budgets-form="">
      <Group
        icon={Building2}
        title="Organization"
        description="One limit on everyone's usage together. You can set a monthly limit, a daily one, or both."
      >
        {rows("tenant").map((row) => (
          <RowFields key={row.key} row={row} title={`${PERIOD_ADJECTIVES[row.period]} organization budget`} errors={errors} status={row.id ? status.get(row.id) : undefined} onChange={patchRow} onRemove={removeRow} />
        ))}
        {rows("tenant").length === 0 ? <p className="text-sm text-muted-foreground">No organization budget: usage as a whole has no limit.</p> : null}
        {rows("tenant").length < 2 ? (
          <Button
            variant="outline"
            size="sm"
            className="w-fit"
            onClick={() => addRow(newRow("tenant", null, rows("tenant").some((r) => r.period === "month") ? "day" : "month"))}
          >
            <Plus data-icon="inline-start" />
            Add an organization budget
          </Button>
        ) : null}
      </Group>

      <Group icon={UserRound} title="Default cap per person" description="Applies to everyone who has no cap of their own below.">
        <DefaultCapFields cap={form.userDefault} error={errors["default.limit"] ?? null} onChange={patchDefault} />
      </Group>

      <Group icon={UserRound} title="Caps for specific people" description="Replace the default cap for these people.">
        {rows("user").map((row) => (
          <RowFields key={row.key} row={row} title={row.subject?.name ?? "Choose a person"} errors={errors} status={row.id ? status.get(row.id) : undefined} onChange={patchRow} onRemove={removeRow} />
        ))}
        <div className="max-w-md">
          <ColleaguePicker
            label="Add a cap for a person"
            exclude={new Set(rows("user").flatMap((row) => (row.subject ? [row.subject.id] : [])))}
            onPick={(person) => addRow(newRow("user", { id: person.id, name: person.display_name }))}
          />
          <p className="mt-1 text-xs text-muted-foreground">Search by name or email to add a cap for someone.</p>
        </div>
      </Group>

      <Group
        icon={Building2}
        title="Department budgets"
        badge={<EnterpriseBadge feature="department_budgets" />}
        description="A limit on the usage of everyone in a department."
      >
        <EnterpriseGateView feature="department_budgets" edition={edition} loading={editionLoading}>
          {departmentRows.map((row) => (
            <RowFields key={row.key} row={row} title={row.subject?.name ?? "Choose a department"} errors={errors} status={row.id ? status.get(row.id) : undefined} onChange={patchRow} onRemove={removeRow} />
          ))}
          <AddDepartment
            taken={new Set(departmentRows.flatMap((row) => (row.subject ? [`${row.subject.id}:${row.period}`] : [])))}
            onAdd={(department, period) => addRow(newRow("department", department, period))}
          />
        </EnterpriseGateView>
        {droppedDepartments ? (
          <Alert>
            <Info aria-hidden="true" />
            <AlertTitle>
              {departmentRows.length} saved department {departmentRows.length === 1 ? "budget" : "budgets"}
            </AlertTitle>
            <AlertDescription>
              <p>
                {departmentRows.map((row) => row.subject?.name ?? "a department").join(", ")}. The API only accepts department budgets with an
                Enterprise license that includes them, so saving this page removes {departmentRows.length === 1 ? "it" : "them"}.
              </p>
            </AlertDescription>
          </Alert>
        ) : null}
      </Group>

      <div className="sticky bottom-0 z-10 -mx-1 flex flex-col gap-3 border-t bg-background/95 px-1 py-3 backdrop-blur">
        {problem?.kind === "enterprise" ? <EnterpriseNotice feature="department_budgets" /> : null}
        {problem?.kind === "invalid" ? (
          <Alert variant="destructive">
            <AlertTitle>The API didn&apos;t accept these budgets</AlertTitle>
            <AlertDescription>
              {problem.general.length > 0 ? (
                <ul className="list-disc pl-4">
                  {problem.general.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              ) : (
                <p>Fix the highlighted fields and save again.</p>
              )}
            </AlertDescription>
          </Alert>
        ) : null}
        {problem?.kind === "error" ? <ErrorAlert error={problem.error} /> : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => void save()} disabled={saving || !dirty}>
            {saving ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : null}
            {saving ? "Saving…" : "Save budgets"}
          </Button>
          <Button
            variant="ghost"
            disabled={saving || !dirty}
            onClick={() => {
              setForm(formFromBudgets(saved));
              setAttempted(false);
              setServerErrors({});
              setProblem(null);
            }}
          >
            <Undo2 data-icon="inline-start" />
            Discard changes
          </Button>
          <p className="text-xs text-muted-foreground" role="status">
            {attempted && errorCount > 0
              ? `${errorCount} ${errorCount === 1 ? "field needs" : "fields need"} fixing before saving.`
              : dirty
                ? "Unsaved changes."
                : "Everything is saved."}
          </p>
        </div>
      </div>
    </div>
  );
}

function Group({
  icon: Icon,
  title,
  description,
  badge,
  children,
}: {
  icon: typeof Building2;
  title: string;
  description: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section className="flex flex-col gap-3 rounded-xl border bg-card p-4" aria-labelledby={headingId}>
      <div>
        <h4 id={headingId} className="flex items-center gap-2 font-medium">
          <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
          {title}
          {badge}
        </h4>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {children}
    </section>
  );
}

function LimitInput({ id, value, onChange, describedBy, invalid }: { id: string; value: string; onChange: (value: string) => void; describedBy?: string; invalid: boolean }) {
  return (
    <div className="relative">
      <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-sm text-muted-foreground" aria-hidden="true">
        $
      </span>
      <Input
        id={id}
        inputMode="decimal"
        autoComplete="off"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
        className="w-32 pl-6"
        placeholder="0.00"
      />
    </div>
  );
}

/** One budget's fields, as a fieldset named after the budget so every label has its context. */
export function RowFields({
  row,
  title,
  errors,
  status,
  onChange,
  onRemove,
}: {
  row: BudgetRow;
  title: string;
  errors: FormErrors;
  status?: AdminBudget;
  onChange: (key: string, patch: Partial<BudgetRow>) => void;
  onRemove: (key: string) => void;
}) {
  const error = (field: string) => errors[`${row.key}.${field}`] ?? null;
  return (
    <fieldset className={cn("flex flex-col gap-3 rounded-lg border p-3", error("subject") && "border-destructive")}>
      {/* The legend names the group for screen readers; the visible title sits beside the remove button. */}
      <legend className="sr-only">{title}</legend>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-sm font-medium" aria-hidden="true">
          {title}
        </p>
        <Button variant="ghost" size="icon-sm" onClick={() => onRemove(row.key)} aria-label={`Remove ${title}`}>
          <Trash2 />
        </Button>
      </div>
      {error("subject") ? <p className="text-xs text-destructive">{error("subject")}</p> : null}
      <div className="flex flex-wrap items-start gap-3">
        {/* Fixed widths: a message under a field wraps instead of pushing the row apart. */}
        <Field label="Limit (USD)" error={error("limit")} className="w-40">
          {({ id, describedBy, invalid }) => (
            <LimitInput id={id} value={row.limit} onChange={(limit) => onChange(row.key, { limit })} describedBy={describedBy} invalid={invalid} />
          )}
        </Field>
        <Field label="Period" error={error("period")} className="max-w-xs">
          {({ id, describedBy, invalid }) => (
            <SimpleSelect id={id} value={row.period} onChange={(period) => onChange(row.key, { period })} options={PERIOD_OPTIONS} describedBy={describedBy} invalid={invalid} />
          )}
        </Field>
        <Field label="Warn at (% of the limit)" error={error("warn")} className="w-44">
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="number"
              min={1}
              max={100}
              step={1}
              value={row.warnPercent}
              onChange={(event) => onChange(row.key, { warnPercent: event.target.value })}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
              className="w-24"
            />
          )}
        </Field>
        <Field label="When it is used up" hint={ACTION_HINTS[row.action]} className="max-w-56">
          {({ id, describedBy }) => (
            <SimpleSelect id={id} value={row.action} onChange={(action) => onChange(row.key, { action })} options={ACTION_OPTIONS} describedBy={describedBy} />
          )}
        </Field>
      </div>
      {status ? (
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="tabular-nums">
            {formatUsd(status.spent_usd)} spent {status.period === "day" ? "today" : "this month"}
          </span>
          <BudgetStatusLabel budget={status} className="text-foreground" />
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">Not saved yet.</p>
      )}
    </fieldset>
  );
}

export function DefaultCapFields({ cap, error, onChange }: { cap: DefaultCapForm; error: string | null; onChange: (patch: Partial<DefaultCapForm>) => void }) {
  return (
    <div className="flex flex-col gap-3">
      <label className="flex w-fit items-center gap-2 text-sm">
        <Switch checked={cap.enabled} onCheckedChange={(enabled) => onChange({ enabled })} aria-label="Cap every person's usage" />
        {cap.enabled ? "Everyone has a cap" : "No default cap: people without a cap of their own have no personal limit"}
      </label>
      {cap.enabled ? (
        <div className="flex flex-wrap items-start gap-3">
          <Field label="Limit per person (USD)" error={error} className="w-44">
            {({ id, describedBy, invalid }) => (
              <LimitInput id={id} value={cap.limit} onChange={(limit) => onChange({ limit })} describedBy={describedBy} invalid={invalid} />
            )}
          </Field>
          <Field label="Period" className="max-w-xs">
            {({ id, describedBy }) => <SimpleSelect id={id} value={cap.period} onChange={(period) => onChange({ period })} options={PERIOD_OPTIONS} describedBy={describedBy} />}
          </Field>
          <Field label="When it is used up" hint={ACTION_HINTS[cap.action]} className="max-w-56">
            {({ id, describedBy }) => <SimpleSelect id={id} value={cap.action} onChange={(action) => onChange({ action })} options={ACTION_OPTIONS} describedBy={describedBy} />}
          </Field>
        </div>
      ) : null}
    </div>
  );
}

const NONE = "";

function AddDepartment({
  taken,
  onAdd,
}: {
  taken: ReadonlySet<string>;
  onAdd: (department: { id: string; name: string }, period: BudgetPeriod) => void;
}) {
  const departments = useApiQuery(adminKeys.departments, adminApi.listDepartments);
  const [pick, setPick] = useState<string>(NONE);
  if (departments.state.status === "loading") return <TableSkeleton rows={1} />;
  if (departments.state.status === "error") return <ErrorAlert error={departments.state.error} onRetry={() => void departments.refetch()} />;
  const list: Department[] = departments.state.data.items;
  // A department can have a monthly and a daily budget; it is offered while one of them is free.
  const options = list
    .filter((d) => !(taken.has(`${d.id}:month`) && taken.has(`${d.id}:day`)))
    .map((d) => ({ value: d.id, label: d.name }));
  if (list.length === 0) return <p className="text-sm text-muted-foreground">There are no departments yet. Create them in Admin › Departments.</p>;
  const chosen = list.find((d) => d.id === pick);
  return (
    <div className="flex flex-wrap items-end gap-2">
      <Field label="Add a budget for a department">
        {({ id }) => (
          <SimpleSelect id={id} value={pick} onChange={setPick} options={[{ value: NONE, label: "Choose a department", disabled: true }, ...options]} className="min-w-56" />
        )}
      </Field>
      <Button
        variant="outline"
        disabled={!chosen}
        onClick={() => {
          if (!chosen) return;
          onAdd({ id: chosen.id, name: chosen.name }, taken.has(`${chosen.id}:month`) ? "day" : "month");
          setPick(NONE);
        }}
      >
        <Plus data-icon="inline-start" />
        Add
      </Button>
    </div>
  );
}
