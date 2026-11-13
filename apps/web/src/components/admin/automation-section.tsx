"use client";

import { BellRing, Circle, CircleCheck, Clock, MessageSquare, Plus, Route, ShieldCheck, Trash2, TriangleAlert, X } from "lucide-react";
import { useId, useState } from "react";

import { Field, SimpleSelect } from "@/components/data/form";
import { ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useApiQuery } from "@/hooks/use-api-query";
import { adminApi, adminKeys } from "@/lib/admin/api";
import {
  automationApi,
  automationKeys,
  describeDraft,
  draftProblems,
  draftToRule,
  ESCALATE_TO_LABELS,
  KNOWN_ACTION_TOOLS,
  rowsChanged,
  rowsFrom,
  ruleToDraft,
  rulesToSave,
  SELECTOR_LABELS,
  type AutomationSettings,
  type EscalateTo,
  type EscalationPolicy,
  type RoutingPolicy,
  type RoutingPreview,
  type RoutingRule,
  type RuleDraft,
  type RuleRow,
  type SelectorKind,
  type SlackSettings,
} from "@/lib/admin/automation";
import { getQueryCache } from "@/lib/stores/query-cache";
import { toast } from "@/lib/stores/toasts";
import type { NotificationCategory } from "@/lib/work/preferences";
import { destination, routingLine } from "@/lib/work/requests";

type DepartmentChoice = { key: string; name: string };

/** Admin › Automation (docs/automation.md). The API enforces every rule; this edits them. */
export function AutomationSection() {
  const settings = useApiQuery(automationKeys.settings, automationApi.get);
  const departments = useApiQuery(adminKeys.departments, adminApi.listDepartments);
  const choices: DepartmentChoice[] =
    departments.state.status === "success" ? departments.state.data.items.map((d) => ({ key: d.slug, name: d.name })) : [];

  return (
    <section className="flex flex-col gap-6" aria-labelledby="automation-heading">
      <div>
        <h3 id="automation-heading" className="flex items-center gap-2 text-lg font-semibold">
          Automation
          <RefreshingHint active={settings.state.status === "success" && settings.state.refreshing} />
        </h3>
        <p className="max-w-3xl text-sm text-muted-foreground">
          What happens to approvals and tasks that wait, who approves the assistant&apos;s actions, where requests go, and Slack.
          Every reminder, escalation, routing decision and change here is in the audit log.
        </p>
      </div>
      {settings.state.status === "loading" ? (
        <TableSkeleton rows={4} />
      ) : settings.state.status === "error" ? (
        <ErrorAlert error={settings.state.error} onRetry={() => void settings.refetch()} />
      ) : (
        <>
          <EscalationCard saved={settings.state.data} />
          <ApproverRulesCard saved={settings.state.data} departments={choices} />
          <RoutingCard saved={settings.state.data} departments={choices} />
        </>
      )}
      <SlackCard />
    </section>
  );
}

/** Saves the whole settings document with one part changed (the API takes all three parts). */
async function saveSettings(next: AutomationSettings, what: string): Promise<void> {
  const stored = await automationApi.save(next);
  getQueryCache().setData<AutomationSettings>(automationKeys.settings, () => stored);
  toast({ title: `${what} saved` });
}

// --- Escalation -------------------------------------------------------------------------------

const HOURS_FIELDS: readonly { key: keyof EscalationPolicy; label: string; hint: string }[] = [
  { key: "escalate_after_hours", label: "Escalate an approval after", hint: "Hours without a decision. 0: never." },
  { key: "remind_before_hours", label: "Remind the approver", hint: "Hours before the approval expires (3 days after it was asked). 0: no reminder." },
  { key: "task_remind_before_hours", label: "Remind about a task due", hint: "Hours before its due date. 0: no reminder." },
  { key: "task_escalate_after_hours", label: "Escalate an overdue task after", hint: "Hours past its due date. 0: never." },
  { key: "queue_escalate_after_hours", label: "Escalate an unclaimed queue task after", hint: "Hours nobody claimed it; its team's managers are told. 0: never." },
];

function EscalationCard({ saved }: { saved: AutomationSettings }) {
  const [draft, setDraft] = useState<EscalationPolicy>(saved.escalation);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved.escalation);
  const invalid = HOURS_FIELDS.some((f) => {
    const value = draft[f.key] as number;
    return !Number.isInteger(value) || value < 0 || value > 720;
  });

  async function save() {
    setPending(true);
    setError(null);
    try {
      await saveSettings({ ...saved, escalation: draft }, "Reminders and escalation");
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Clock className="size-4" aria-hidden="true" />
          Reminders and escalation
        </CardTitle>
        <CardDescription>
          Checked every hour; each reminder and escalation happens once per approval (each step of a chain counts on its own) and once
          per task. An escalated approval goes to someone else as well; the first decision counts. Expiry stays the last step: nothing runs.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!invalid) void save();
          }}
        >
          <label className="flex items-center gap-3 text-sm">
            <Switch checked={draft.enabled} disabled={pending} onCheckedChange={(enabled) => setDraft((d) => ({ ...d, enabled }))} />
            Send reminders and escalate
          </label>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {HOURS_FIELDS.map((field) => {
              const value = draft[field.key] as number;
              const wrong = !Number.isInteger(value) || value < 0 || value > 720;
              return (
                <Field key={field.key} label={`${field.label} (hours)`} hint={field.hint} error={wrong ? "A whole number of hours, 0 to 720." : null}>
                  {({ id, describedBy, invalid: isInvalid }) => (
                    <Input
                      id={id}
                      type="number"
                      inputMode="numeric"
                      min={0}
                      max={720}
                      value={Number.isNaN(value) ? "" : value}
                      disabled={pending || !draft.enabled}
                      onChange={(e) => setDraft((d) => ({ ...d, [field.key]: e.target.value === "" ? Number.NaN : Number(e.target.value) }))}
                      aria-describedby={describedBy}
                      aria-invalid={isInvalid || undefined}
                    />
                  )}
                </Field>
              );
            })}
            <Field label="Escalate to" hint="The approver's backup (set in their Settings), their department's managers, the org admins: from the one chosen, down the line.">
              {({ id, describedBy }) => (
                <SimpleSelect<EscalateTo>
                  id={id}
                  className="w-full min-w-0"
                  describedBy={describedBy}
                  value={draft.escalate_to}
                  disabled={pending || !draft.enabled}
                  onChange={(escalate_to) => setDraft((d) => ({ ...d, escalate_to }))}
                  options={(Object.keys(ESCALATE_TO_LABELS) as EscalateTo[]).map((key) => ({ value: key, label: ESCALATE_TO_LABELS[key] }))}
                />
              )}
            </Field>
          </div>
          <p className="text-xs text-muted-foreground">
            Not escalated: an approval waiting for the person who asked (they confirm their own assistant&apos;s action) and Knowledge Hub
            requests for official status (they already go to the collection&apos;s owner). Both are still reminded. People set their own
            backup approver in Settings.
          </p>
          {error ? <ErrorAlert error={error} /> : null}
          <div className="flex justify-end">
            <Button type="submit" disabled={pending || !dirty || invalid}>
              {pending ? "Saving…" : "Save reminders and escalation"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// --- Approver rules ----------------------------------------------------------------------------

function ApproverRulesCard({ saved, departments }: { saved: AutomationSettings; departments: DepartmentChoice[] }) {
  const tools = [...new Set([...KNOWN_ACTION_TOOLS.map((t) => t.tool), ...Object.keys(saved.approver_rules)])];
  const [drafts, setDrafts] = useState<Record<string, RuleDraft | null>>(() =>
    Object.fromEntries(tools.map((tool) => [tool, ruleToDraft(saved.approver_rules[tool])])),
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const departmentName = (key: string) => departments.find((d) => d.key === key)?.name ?? key;
  const problems = Object.entries(drafts).flatMap(([tool, draft]) => (draft ? draftProblems(draft).map((p) => `${tool}: ${p}`) : []));

  function rules(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const [tool, draft] of Object.entries(drafts)) {
      if (draft === null) out[tool] = saved.approver_rules[tool]; // shown as written, kept as is
      else {
        const rule = draftToRule(draft);
        if (rule !== null) out[tool] = rule;
      }
    }
    return out;
  }
  const dirty = JSON.stringify(rules()) !== JSON.stringify(saved.approver_rules);

  async function save() {
    setPending(true);
    setError(null);
    try {
      await saveSettings({ ...saved, approver_rules: rules() }, "Approver rules");
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" aria-hidden="true" />
          Who approves the assistant&apos;s actions
        </CardTitle>
        <CardDescription>
          By default the person who asked confirms their own assistant&apos;s action. A rule sends it to others instead: everyone in a
          step can decide it (the first decision counts), and each step must approve, in order, before it runs. A rule never includes
          the person who asked. Workflow steps use the same rules, or their own <code>approver</code>.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (problems.length === 0) void save();
          }}
        >
          {tools.map((tool) => {
            const label = KNOWN_ACTION_TOOLS.find((t) => t.tool === tool)?.label ?? tool;
            const draft = drafts[tool];
            return (
              <fieldset key={tool} className="flex flex-col gap-2 rounded-lg border p-3">
                <legend className="px-1 text-sm font-medium">
                  {label} <code className="text-xs text-muted-foreground">{tool}</code>
                </legend>
                {draft === null ? (
                  <div className="flex flex-col gap-2 text-sm">
                    <p className="text-muted-foreground">This rule was set through the API in a form this page can&apos;t edit:</p>
                    <pre className="overflow-auto rounded bg-muted/50 p-2 font-mono text-xs">{JSON.stringify(saved.approver_rules[tool], null, 2)}</pre>
                    <Button type="button" size="sm" variant="outline" className="w-fit" onClick={() => setDrafts((d) => ({ ...d, [tool]: [] }))}>
                      Replace it
                    </Button>
                  </div>
                ) : (
                  <RuleBuilder
                    tool={label}
                    draft={draft}
                    departments={departments}
                    disabled={pending}
                    onChange={(next) => setDrafts((d) => ({ ...d, [tool]: next }))}
                  />
                )}
                {draft ? <p className="text-xs text-muted-foreground">Approved by: {describeDraft(draft, departmentName)}.</p> : null}
              </fieldset>
            );
          })}
          {problems.length > 0 ? (
            <ul className="list-disc pl-5 text-xs text-destructive">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          ) : null}
          {error ? <ErrorAlert error={error} /> : null}
          <div className="flex justify-end">
            <Button type="submit" disabled={pending || !dirty || problems.length > 0}>
              {pending ? "Saving…" : "Save approver rules"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

const SELECTOR_KINDS = Object.keys(SELECTOR_LABELS) as SelectorKind[];

function RuleBuilder({
  tool,
  draft,
  departments,
  disabled,
  onChange,
}: {
  tool: string;
  draft: RuleDraft;
  departments: DepartmentChoice[];
  disabled: boolean;
  onChange: (draft: RuleDraft) => void;
}) {
  const update = (stage: number, index: number, change: { kind?: SelectorKind; value?: string }) =>
    onChange(
      draft.map((s, i) =>
        i !== stage ? s : s.map((sel, j) => (j !== index ? sel : { kind: change.kind ?? sel.kind, value: change.kind && change.kind !== sel.kind ? "" : (change.value ?? sel.value) })),
      ),
    );
  if (draft.length === 0) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">The person who asked approves it.</span>
        <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={() => onChange([[{ kind: "requester_manager", value: "" }]])}>
          <Plus data-icon="inline-start" aria-hidden="true" />
          Add a rule
        </Button>
      </div>
    );
  }
  return (
    <ol className="flex flex-col gap-3">
      {draft.map((stage, stageIndex) => (
        <li key={stageIndex} className="flex flex-col gap-2 rounded-md bg-muted/30 p-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted-foreground">
              {draft.length > 1 ? `Step ${stageIndex + 1}` : "Approvers"}
              {stage.length > 1 ? " (any one of them)" : ""}
            </span>
            {draft.length > 1 ? (
              <Button
                type="button"
                size="xs"
                variant="ghost"
                disabled={disabled}
                aria-label={`Remove step ${stageIndex + 1} of the ${tool} rule`}
                onClick={() => onChange(draft.filter((_, i) => i !== stageIndex))}
              >
                <Trash2 aria-hidden="true" />
                Remove step
              </Button>
            ) : null}
          </div>
          {stage.map((selector, index) => (
            <div key={index} className="flex flex-wrap items-center gap-2">
              <SimpleSelect<SelectorKind>
                size="sm"
                ariaLabel={`Who, step ${stageIndex + 1}, approver ${index + 1}`}
                value={selector.kind}
                disabled={disabled}
                onChange={(kind) => update(stageIndex, index, { kind })}
                options={SELECTOR_KINDS.map((kind) => ({ value: kind, label: SELECTOR_LABELS[kind] }))}
              />
              {selector.kind === "user" ? (
                <Input
                  type="email"
                  className="h-7 w-64"
                  aria-label={`Email of approver ${index + 1}, step ${stageIndex + 1}`}
                  placeholder="name@company.example"
                  value={selector.value}
                  disabled={disabled}
                  onChange={(e) => update(stageIndex, index, { value: e.target.value })}
                />
              ) : null}
              {selector.kind === "department_manager" ? (
                <SimpleSelect
                  size="sm"
                  ariaLabel={`Department, step ${stageIndex + 1}, approver ${index + 1}`}
                  value={selector.value}
                  disabled={disabled}
                  onChange={(value) => update(stageIndex, index, { value })}
                  options={[{ value: "", label: "Choose a department", disabled: true }, ...departments.map((d) => ({ value: d.key, label: d.name }))]}
                />
              ) : null}
              <Button
                type="button"
                size="icon-xs"
                variant="ghost"
                disabled={disabled}
                aria-label={`Remove approver ${index + 1} of step ${stageIndex + 1}`}
                onClick={() => onChange(draft.map((s, i) => (i !== stageIndex ? s : s.filter((_, j) => j !== index))).filter((s) => s.length > 0))}
              >
                <X aria-hidden="true" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            size="xs"
            variant="ghost"
            className="w-fit"
            disabled={disabled}
            onClick={() => onChange(draft.map((s, i) => (i !== stageIndex ? s : [...s, { kind: "org_admins", value: "" }])))}
          >
            <Plus data-icon="inline-start" aria-hidden="true" />
            Or someone else
          </Button>
        </li>
      ))}
      <li>
        <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={() => onChange([...draft, [{ kind: "org_admins", value: "" }]])}>
          <Plus data-icon="inline-start" aria-hidden="true" />
          Then another step
        </Button>
      </li>
    </ol>
  );
}

// --- Request routing ---------------------------------------------------------------------------

function RoutingCard({ saved, departments }: { saved: AutomationSettings; departments: DepartmentChoice[] }) {
  const rules = useApiQuery(automationKeys.routingRules, automationApi.routingRules);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Route className="size-4" aria-hidden="true" />
          Request routing
        </CardTitle>
        <CardDescription>
          Requests (the Requests page, email, the assistant&apos;s tasks without a team) go to a department&apos;s queue. Rules decide
          first; when none does, the router model may, if it is confident enough; anything else waits in triage, where org admins route it.
          Each decision is kept with the task and audited.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <RoutingPolicyForm saved={saved} departments={departments} />
        {rules.state.status === "loading" ? (
          <TableSkeleton rows={3} />
        ) : rules.state.status === "error" ? (
          <ErrorAlert error={rules.state.error} onRetry={() => void rules.refetch()} />
        ) : (
          <RoutingRulesEditor key={JSON.stringify(rules.state.data)} saved={rules.state.data} departments={departments} />
        )}
        <RoutingPreviewForm />
      </CardContent>
    </Card>
  );
}

function RoutingPolicyForm({ saved, departments }: { saved: AutomationSettings; departments: DepartmentChoice[] }) {
  const [draft, setDraft] = useState<RoutingPolicy>(saved.routing);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved.routing);
  const badThreshold = !(draft.threshold >= 0 && draft.threshold <= 1);

  async function save() {
    setPending(true);
    setError(null);
    try {
      await saveSettings({ ...saved, routing: draft }, "Routing settings");
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!badThreshold) void save();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <label className="flex items-center gap-3 text-sm sm:col-span-3">
          <Switch checked={draft.use_model} disabled={pending} onCheckedChange={(use_model) => setDraft((d) => ({ ...d, use_model }))} />
          Let the router model route what no rule does
        </label>
        <Field label="Confidence it needs (0 to 1)" hint="Below this, the request goes to triage." error={badThreshold ? "A number from 0 to 1." : null}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="number"
              step={0.05}
              min={0}
              max={1}
              value={Number.isNaN(draft.threshold) ? "" : draft.threshold}
              disabled={pending || !draft.use_model}
              onChange={(e) => setDraft((d) => ({ ...d, threshold: e.target.value === "" ? Number.NaN : Number(e.target.value) }))}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>
        <Field label="Triage, besides the org admins" hint="Its members see and route requests in triage too.">
          {({ id, describedBy }) => (
            <SimpleSelect
              id={id}
              describedBy={describedBy}
              value={draft.triage_department ?? ""}
              disabled={pending}
              onChange={(value) => setDraft((d) => ({ ...d, triage_department: value || null }))}
              options={[{ value: "", label: "Nobody else" }, ...departments.map((d) => ({ value: d.key, label: d.name }))]}
            />
          )}
        </Field>
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={pending || !dirty || badThreshold}>
          {pending ? "Saving…" : "Save routing settings"}
        </Button>
      </div>
    </form>
  );
}

let newRowCount = 0;

function RoutingRulesEditor({ saved, departments }: { saved: RoutingRule[]; departments: DepartmentChoice[] }) {
  const [rows, setRows] = useState<RuleRow[]>(() => rowsFrom(saved));
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const dirty = rowsChanged(rows, saved);

  function change(index: number, patch: Partial<RuleRow>) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  async function save() {
    const checked = rulesToSave(rows);
    if (!checked.ok) {
      setErrors(checked.errors);
      return;
    }
    setErrors({});
    setPending(true);
    setError(null);
    try {
      const stored = await automationApi.saveRoutingRules(checked.rules);
      getQueryCache().setData<RoutingRule[]>(automationKeys.routingRules, () => stored);
      toast({ title: "Routing rules saved", description: `${stored.length} ${stored.length === 1 ? "rule" : "rules"}` });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div>
        <h4 className="text-sm font-medium">Rules</h4>
        <p className="text-xs text-muted-foreground">
          A keyword matches whole words in the request&apos;s title and details (case ignored); a category is chosen on the Request form
          and outweighs keywords. The department with the most matches gets it; a tie goes to the model. Separate terms with commas.
        </p>
      </div>
      {rows.length === 0 ? <p className="text-sm text-muted-foreground">No rules yet: every request goes to the model, then triage.</p> : null}
      <ul className="flex flex-col gap-3">
        {rows.map((row, index) => (
          <li key={row.key} className="grid gap-2 rounded-lg border p-3 sm:grid-cols-[12rem_1fr_1fr_auto] sm:items-start">
            <Field label="Department" error={errors[index] ?? null}>
              {({ id, describedBy, invalid }) => (
                <SimpleSelect
                  id={id}
                  size="sm"
                  describedBy={describedBy}
                  invalid={invalid}
                  value={row.department}
                  disabled={pending}
                  onChange={(department) => change(index, { department })}
                  options={[{ value: "", label: "Choose", disabled: true }, ...departments.map((d) => ({ value: d.key, label: d.name }))]}
                />
              )}
            </Field>
            <Field label="Keywords">
              {({ id, describedBy }) => (
                <Textarea id={id} rows={2} value={row.keywords} disabled={pending} onChange={(e) => change(index, { keywords: e.target.value })} aria-describedby={describedBy} placeholder="invoice, refund, expense report" />
              )}
            </Field>
            <Field label="Categories">
              {({ id, describedBy }) => (
                <Textarea id={id} rows={2} value={row.categories} disabled={pending} onChange={(e) => change(index, { categories: e.target.value })} aria-describedby={describedBy} placeholder="Billing and expenses" />
              )}
            </Field>
            <div className="flex items-center gap-2 sm:flex-col sm:items-end sm:pt-6">
              <label className="flex items-center gap-2 text-xs">
                <Switch checked={row.enabled} disabled={pending} onCheckedChange={(enabled) => change(index, { enabled })} />
                On
              </label>
              <Button type="button" size="icon-sm" variant="ghost" disabled={pending} aria-label={`Remove the rule for ${row.department || "a department"}`} onClick={() => setRows((r) => r.filter((_, i) => i !== index))}>
                <Trash2 aria-hidden="true" />
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {error ? <ErrorAlert error={error} /> : null}
      <div className="flex flex-wrap justify-between gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={pending}
          onClick={() => setRows((r) => [...r, { key: `new-${(newRowCount += 1)}`, department: "", keywords: "", categories: "", enabled: true }])}
        >
          <Plus data-icon="inline-start" aria-hidden="true" />
          Add a rule
        </Button>
        <Button type="submit" size="sm" disabled={pending || !dirty}>
          {pending ? "Saving…" : "Save rules"}
        </Button>
      </div>
    </form>
  );
}

function RoutingPreviewForm() {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<RoutingPreview | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function preview() {
    if (!title.trim()) return;
    setPending(true);
    setError(null);
    try {
      setResult(await automationApi.preview({ title: title.trim(), description: description.trim() }));
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-3 rounded-lg border bg-muted/20 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        void preview();
      }}
    >
      <div>
        <h4 className="text-sm font-medium">Try a request</h4>
        <p className="text-xs text-muted-foreground">Where it would go with the saved rules and settings, and why. Nothing is created.</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Title">{({ id }) => <Input id={id} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />}</Field>
        <Field label="Details">{({ id }) => <Input id={id} value={description} maxLength={2000} onChange={(e) => setDescription(e.target.value)} />}</Field>
      </div>
      <div aria-live="polite">
        {result ? (
          <p className="text-sm">
            <span className="font-medium">{result.method === "triage" ? "Triage" : destination(result)}</span> · {routingLine(result)}.{" "}
            <span className="text-muted-foreground">{result.explanation}</span>
          </p>
        ) : null}
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      <Button type="submit" size="sm" variant="outline" className="w-fit" disabled={pending || !title.trim()}>
        {pending ? "Checking…" : "Where would it go?"}
      </Button>
    </form>
  );
}

// --- Slack -------------------------------------------------------------------------------------

const CHANNEL_CATEGORIES: readonly { key: NotificationCategory; label: string }[] = [
  { key: "approvals", label: "Approvals" },
  { key: "assignments", label: "Assignments and requests" },
  { key: "escalations", label: "Escalations" },
  { key: "reviews", label: "Document reviews" },
  { key: "sources", label: "Sources" },
  { key: "legal_holds", label: "Legal holds" },
  { key: "budgets", label: "Budget warnings" },
  { key: "mentions", label: "Mentions" },
  { key: "other", label: "Everything else" },
];

function SlackCard() {
  const { state, refetch } = useApiQuery(automationKeys.slack, automationApi.slack);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageSquare className="size-4" aria-hidden="true" />
          Slack
        </CardTitle>
        <CardDescription>
          A bot token lets people get their notifications as Slack direct messages (they choose which in Settings; Slack finds them by
          email). An incoming webhook posts the categories you pick to one channel. Both are stored encrypted and never shown again.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {state.status === "loading" ? (
          <TableSkeleton rows={2} />
        ) : state.status === "error" ? (
          <ErrorAlert error={state.error} onRetry={() => void refetch()} />
        ) : (
          <SlackForm key={JSON.stringify(state.data)} saved={state.data} />
        )}
      </CardContent>
    </Card>
  );
}

function SlackForm({ saved }: { saved: SlackSettings }) {
  const tokenId = useId();
  const hookId = useId();
  const [token, setToken] = useState("");
  const [hook, setHook] = useState("");
  const [categories, setCategories] = useState<NotificationCategory[]>(saved.channel_categories);
  const [pending, setPending] = useState(false);
  const [testing, setTesting] = useState<"bot" | "webhook" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const dirty = token.trim() !== "" || hook.trim() !== "" || JSON.stringify(categories) !== JSON.stringify(saved.channel_categories);

  async function store(body: Parameters<typeof automationApi.saveSlack>[0], what: string) {
    setPending(true);
    setError(null);
    try {
      const stored = await automationApi.saveSlack(body);
      getQueryCache().setData<SlackSettings>(automationKeys.slack, () => stored);
      toast({ title: what });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  async function test(target: "bot" | "webhook") {
    setTesting(target);
    try {
      const outcome = await automationApi.testSlack(target);
      toast({ title: outcome.ok ? "Slack works" : "Slack refused the test", description: outcome.detail, tone: outcome.ok ? undefined : "error" });
    } catch (err) {
      setError(err);
    } finally {
      setTesting(null);
    }
  }

  function save() {
    const body: Parameters<typeof automationApi.saveSlack>[0] = { channel_categories: categories };
    if (token.trim()) body.bot_token = token.trim();
    if (hook.trim()) body.webhook_url = hook.trim();
    void store(body, "Slack settings saved");
  }

  return (
    <form
      className="flex flex-col gap-4"
      autoComplete="off"
      onSubmit={(event) => {
        event.preventDefault();
        save();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={tokenId} className="flex items-center gap-2 text-sm font-medium">
            Bot token <ConfiguredBadge on={saved.bot_configured} />
          </label>
          <Input id={tokenId} type="password" autoComplete="off" spellCheck={false} placeholder={saved.bot_configured ? "Saved; type to replace it" : "xoxb-…"} value={token} disabled={pending} onChange={(e) => setToken(e.target.value)} aria-describedby={`${tokenId}-hint`} />
          <p id={`${tokenId}-hint`} className="text-xs text-muted-foreground">
            A Slack app&apos;s bot token with the <code>users:read.email</code> and <code>chat:write</code> scopes.
          </p>
          {saved.bot_configured ? (
            <div className="flex gap-2">
              <Button type="button" size="xs" variant="outline" disabled={pending || testing !== null} onClick={() => void test("bot")}>
                {testing === "bot" ? "Sending…" : "Send me a test message"}
              </Button>
              <Button type="button" size="xs" variant="ghost" disabled={pending} onClick={() => void store({ bot_token: null, channel_categories: categories }, "Slack bot token removed")}>
                Remove
              </Button>
            </div>
          ) : null}
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={hookId} className="flex items-center gap-2 text-sm font-medium">
            Incoming webhook <ConfiguredBadge on={saved.webhook_configured} />
          </label>
          <Input id={hookId} type="password" autoComplete="off" spellCheck={false} placeholder={saved.webhook_configured ? "Saved; type to replace it" : `${saved.webhook_prefix}services/…`} value={hook} disabled={pending} onChange={(e) => setHook(e.target.value)} aria-describedby={`${hookId}-hint`} />
          <p id={`${hookId}-hint`} className="text-xs text-muted-foreground">
            Must start with <code>{saved.webhook_prefix}</code>.
          </p>
          {saved.webhook_configured ? (
            <div className="flex gap-2">
              <Button type="button" size="xs" variant="outline" disabled={pending || testing !== null} onClick={() => void test("webhook")}>
                {testing === "webhook" ? "Posting…" : "Post a test message"}
              </Button>
              <Button type="button" size="xs" variant="ghost" disabled={pending} onClick={() => void store({ webhook_url: null, channel_categories: categories }, "Slack webhook removed")}>
                Remove
              </Button>
            </div>
          ) : null}
        </div>
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">Posted to the channel</legend>
        <p className="text-xs text-muted-foreground">
          Once per thing (not once per person told), with its title and a link. Everyone in the channel reads the titles, so pick
          categories whose titles they may see.
        </p>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {CHANNEL_CATEGORIES.map((category) => (
            <label key={category.key} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={categories.includes(category.key)}
                disabled={pending}
                onCheckedChange={(checked) =>
                  setCategories((current) =>
                    checked === true ? CHANNEL_CATEGORIES.map((c) => c.key).filter((k) => current.includes(k) || k === category.key) : current.filter((k) => k !== category.key),
                  )
                }
              />
              {category.label}
            </label>
          ))}
        </div>
      </fieldset>
      {categories.length > 0 && !saved.webhook_configured && !hook.trim() ? (
        <Alert>
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>No channel yet</AlertTitle>
          <AlertDescription>Add an incoming webhook, or nothing is posted.</AlertDescription>
        </Alert>
      ) : null}
      {error ? <ErrorAlert error={error} /> : null}
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <BellRing className="size-3.5" aria-hidden="true" />
          A failed message is retried four times over about two and a half hours, then given up; the in-app notification is always there.
        </p>
        <Button type="submit" disabled={pending || !dirty}>
          {pending ? "Saving…" : "Save Slack settings"}
        </Button>
      </div>
    </form>
  );
}

function ConfiguredBadge({ on }: { on: boolean }) {
  return on ? (
    <Badge variant="secondary" className="bg-emerald-500/15 font-normal text-emerald-800 dark:text-emerald-300">
      <CircleCheck data-icon="inline-start" aria-hidden="true" />
      Saved
    </Badge>
  ) : (
    <Badge variant="secondary" className="font-normal">
      <Circle data-icon="inline-start" aria-hidden="true" />
      Not set
    </Badge>
  );
}
