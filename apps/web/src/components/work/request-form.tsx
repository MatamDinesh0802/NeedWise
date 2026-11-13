"use client";

import { ArrowRight, CircleCheck, Inbox, Route } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import type { Me } from "@/lib/session";
import { workApi, workKeys, type RequestResult } from "@/lib/work/api";
import {
  destination,
  emptyRequestForm,
  REQUEST_LIMITS,
  routingLine,
  toRequestCreate,
  validateRequestForm,
  type RequestForm,
  type RequestFormErrors,
} from "@/lib/work/requests";

/**
 * /requests: ask for something without knowing which team handles it. The admins' rules route
 * it first, then the router model; a request neither can place with confidence waits in triage
 * for a person (docs/automation.md).
 */
export function RequestsView() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <header>
        <h2 className="text-xl font-semibold tracking-tight">Send a request</h2>
        <p className="text-sm text-muted-foreground">
          Describe what you need. WorkMind sends it to the team that handles it, and tells you where it went and why.
        </p>
      </header>
      <SignedInGate purpose="send a request">
        {(me) =>
          me.org_role === "guest" ? (
            <EmptyState
              icon={Inbox}
              title="Requests are for members"
              description="Guests can't send requests to the organization's team queues. Ask your contact at the company instead."
            />
          ) : (
            <RequestFormCard me={me} />
          )
        }
      </SignedInGate>
    </div>
  );
}

function RequestFormCard({ me }: { me: Me }) {
  const options = useApiQuery(workKeys.requestOptions, workApi.requestOptions);
  const [form, setForm] = useState<RequestForm>(emptyRequestForm);
  const [errors, setErrors] = useState<RequestFormErrors>({});
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [result, setResult] = useState<RequestResult | null>(null);
  const categories = options.state.status === "success" ? options.state.data.categories : [];

  function update(change: Partial<RequestForm>) {
    setForm((current) => ({ ...current, ...change }));
  }

  async function submit() {
    const problems = validateRequestForm(form);
    setErrors(problems);
    if (Object.keys(problems).length > 0) return;
    setPending(true);
    setFailure(null);
    try {
      const sent = await workApi.createRequest(toRequestCreate(form));
      setResult(sent);
      setForm(emptyRequestForm());
      await invalidateQueries(workKeys.tasks);
    } catch (error) {
      setFailure(error);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {result ? <RequestSent result={result} meId={me.id} onAnother={() => setResult(null)} /> : null}
      {result ? null : (
        <form
          className="flex flex-col gap-4 rounded-xl border bg-card p-5"
          noValidate
          aria-busy={pending}
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <Field label="What do you need?" error={errors.title}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                maxLength={REQUEST_LIMITS.titleMax}
                value={form.title}
                disabled={pending}
                onChange={(e) => update({ title: e.target.value })}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                placeholder="e.g. Refund for a cancelled training seat"
              />
            )}
          </Field>
          <Field
            label="Details (optional)"
            hint="Mention a colleague with @ and their address (e.g. @wei.zhang@acme.test): they are told if they can see the request."
            error={errors.description}
          >
            {({ id, describedBy, invalid }) => (
              <Textarea
                id={id}
                rows={5}
                maxLength={REQUEST_LIMITS.descriptionMax}
                value={form.description}
                disabled={pending}
                onChange={(e) => update({ description: e.target.value })}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
              />
            )}
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Category (optional)" hint={categories.length ? "Choosing one sends it straight to that team." : "Your admins haven't set up categories."}>
              {({ id, describedBy }) => (
                <SimpleSelect
                  id={id}
                  value={form.category}
                  describedBy={describedBy}
                  disabled={pending || categories.length === 0}
                  onChange={(category) => update({ category })}
                  options={[{ value: "", label: "Let WorkMind decide" }, ...categories.map((c) => ({ value: c, label: c }))]}
                />
              )}
            </Field>
            <Field label="Needed by (optional)" error={errors.due}>
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  type="date"
                  value={form.due}
                  disabled={pending}
                  onChange={(e) => update({ due: e.target.value })}
                  aria-describedby={describedBy}
                  aria-invalid={invalid || undefined}
                />
              )}
            </Field>
          </div>
          {failure ? <ErrorAlert error={failure} /> : null}
          <div className="flex justify-end">
            <Button type="submit" disabled={pending}>
              {pending ? "Sending…" : "Send the request"}
            </Button>
          </div>
        </form>
      )}
      {options.state.status === "error" ? <ErrorAlert error={options.state.error} onRetry={() => void options.refetch()} /> : null}
    </div>
  );
}

/** Where the request went and why; a link to the task (its creator can always open it). */
export function RequestSent({ result, meId, onAnother }: { result: RequestResult; meId: string; onAnother: () => void }) {
  const { routing, task } = result;
  const triaged = routing.method === "triage";
  return (
    <Alert role="status">
      {triaged ? <Route aria-hidden="true" /> : <CircleCheck aria-hidden="true" />}
      <AlertTitle>{triaged ? "Sent. A person will route it." : `Sent to ${destination(routing)}`}</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <p>
          {routingLine(routing)}. {routing.explanation}
        </p>
        <div className="flex flex-wrap gap-2">
          <Link href={`/tasks/${encodeURIComponent(task.id)}`} className={buttonVariants({ size: "sm" })}>
            Open the request
            <ArrowRight data-icon="inline-end" aria-hidden="true" />
          </Link>
          <Button size="sm" variant="outline" onClick={onAnother}>
            Send another
          </Button>
        </div>
        {task.created_by === meId ? <p className="text-xs">You find it under Tasks › Created by me.</p> : null}
      </AlertDescription>
    </Alert>
  );
}
