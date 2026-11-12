"use client";

import { FlaskConical, LoaderCircle, Route, Search, Wrench } from "lucide-react";
import { useState } from "react";

import { Field } from "@/components/data/form";
import { ErrorAlert } from "@/components/data/states";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  AGENT_LIMITS,
  agentsApi,
  formatTestUsage,
  routedByLabel,
  routingSummary,
  stepLabel,
  testRequest,
  type AgentForm,
  type AgentTestResult,
} from "@/lib/admin/agents";

/**
 * Tries the definition in the form, saved or not: which agent(s) the planner picks for a
 * question and why, then the answer this agent gives, with citations and cost. It runs as you,
 * with your permissions narrowed to the agent's collections, and is stored nowhere.
 */
export function AgentTestConsole({
  form,
  savedKey,
  formInvalid,
}: {
  form: AgentForm;
  /** The saved agent the draft stands in for; null while creating. */
  savedKey: string | null;
  /** The definition has errors: the console waits until they are fixed. */
  formInvalid: boolean;
}) {
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<AgentTestResult | null>(null);
  const [asked, setAsked] = useState("");
  const empty = !question.trim();

  async function run() {
    if (empty || formInvalid || pending) return;
    setPending(true);
    setError(null);
    try {
      const request = testRequest(form, question, savedKey);
      setResult(await agentsApi.test(request));
      setAsked(request.question);
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Alert>
        <FlaskConical aria-hidden="true" />
        <AlertDescription>
          Tests the definition as it is in the form, including unsaved changes. It searches as you, so it never sees
          more than you may, and nothing is saved as a conversation. Model calls are made and count toward usage.
        </AlertDescription>
      </Alert>
      {/* Not a <form>: the console sits inside the editor's form, and forms can't nest. */}
      <div className="flex flex-col gap-2" role="group" aria-label="Test question">
        <Field
          label="Question"
          hint={
            formInvalid
              ? "Fix the highlighted fields in Definition first."
              : "Ask it the way an employee would. Ctrl+Enter (⌘+Enter) runs the test."
          }
        >
          {({ id, describedBy }) => (
            <Textarea
              id={id}
              rows={2}
              maxLength={AGENT_LIMITS.questionMax}
              value={question}
              placeholder="e.g. Can I sign a 30,000 euro supplier contract myself?"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  void run();
                }
              }}
              aria-describedby={describedBy}
            />
          )}
        </Field>
        <Button type="button" className="w-fit" disabled={empty || formInvalid || pending} onClick={() => void run()}>
          {pending ? <LoaderCircle data-icon="inline-start" className="animate-spin" aria-hidden="true" /> : null}
          {pending ? "Running…" : "Run test"}
        </Button>
      </div>
      <div aria-live="polite" aria-busy={pending}>
        {error ? <ErrorAlert error={error} /> : null}
        {result && !error ? <TestResultView result={result} question={asked} /> : null}
        {!result && !error && !pending ? (
          <p className="text-sm text-muted-foreground">Results show which agents the planner picks, and the answer.</p>
        ) : null}
      </div>
    </div>
  );
}

export function TestResultView({ result, question }: { result: AgentTestResult; question: string }) {
  const { routing, answer } = result;
  return (
    <div className="flex flex-col gap-4">
      <section aria-labelledby="test-routing-heading" className="flex flex-col gap-2 rounded-xl border p-4">
        <h4 id="test-routing-heading" className="flex items-center gap-2 text-sm font-medium">
          <Route className="size-4" aria-hidden="true" />
          Routing
        </h4>
        <p className="text-sm">
          <span className="text-muted-foreground">“{question}” → </span>
          <strong>{routingSummary(routing)}</strong>
        </p>
        <p className="text-xs text-muted-foreground">
          Decided by the {routedByLabel(routing.routed_by)}. {routing.reason}
        </p>
        {routing.agents.length > 1 || (routing.agents[0] && routing.agents[0].goal !== question) ? (
          <ul className="flex flex-col gap-1 text-xs">
            {routing.agents.map((a) => (
              <li key={a.key}>
                <span className="font-medium">{a.name}:</span> {a.goal}
              </li>
            ))}
          </ul>
        ) : null}
        {routing.tested_agent_picked === false ? (
          <p className="text-xs text-amber-700 dark:text-amber-400">
            The planner did not pick this agent for this question. The answer below shows what it would say if it had.
          </p>
        ) : null}
      </section>

      {answer ? (
        <section aria-labelledby="test-answer-heading" className="flex flex-col gap-3 rounded-xl border p-4">
          <h4 id="test-answer-heading" className="text-sm font-medium">
            Answer from this agent
          </h4>
          {answer.error ? (
            <p className="text-sm text-destructive">The agent could not answer: {answer.error}</p>
          ) : (
            <p className="text-sm whitespace-pre-wrap">{answer.text}</p>
          )}
          {answer.withheld > 0 ? (
            <p className="text-xs text-muted-foreground">
              {answer.withheld} restricted {answer.withheld === 1 ? "source was" : "sources were"} left out by the
              organization&apos;s model policy.
            </p>
          ) : null}
          <div>
            <h5 className="mb-1 text-xs font-medium text-muted-foreground">Citations</h5>
            {answer.citations.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                None. {answer.abstained ? "Nothing in its collections that you can see answers this." : ""}
              </p>
            ) : (
              <ol className="flex flex-col gap-1 text-sm">
                {answer.citations.map((c) => (
                  <li key={`${c.marker}-${c.document_id}`} className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="font-mono">
                      {c.marker}
                    </Badge>
                    <span>{c.title}</span>
                    {c.section ? <span className="text-xs text-muted-foreground">{c.section}</span> : null}
                    <span className="text-xs text-muted-foreground">{c.filename}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
          <div>
            <h5 className="mb-1 text-xs font-medium text-muted-foreground">Steps</h5>
            <ol className="flex flex-col gap-1 text-xs">
              {answer.steps.map((step, i) => (
                <li key={i} className="flex items-center gap-1.5">
                  {step.kind === "search" ? (
                    <Search className="size-3.5 shrink-0" aria-hidden="true" />
                  ) : (
                    <Wrench className="size-3.5 shrink-0" aria-hidden="true" />
                  )}
                  <span className={step.ok ? undefined : "text-muted-foreground"}>{stepLabel(step)}</span>
                </li>
              ))}
            </ol>
          </div>
          <p className="text-xs text-muted-foreground">
            {formatTestUsage(answer.usage)}
            {answer.usage.models.length ? ` · ${answer.usage.models.join(", ")}` : ""}
          </p>
        </section>
      ) : (
        <p className="text-sm text-muted-foreground">No answer: no agent was tested.</p>
      )}
    </div>
  );
}
