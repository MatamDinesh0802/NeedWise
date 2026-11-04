"use client";

import { Bot, MessageSquareText, Settings2 } from "lucide-react";
import Link from "next/link";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { EmptyState, ErrorAlert } from "@/components/data/states";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-api-query";
import type { Me } from "@/lib/session";
import { cn } from "@/lib/utils";
import { workApi, workKeys } from "@/lib/work/api";

/** /agents: what each department agent can help with. Read-only; admins manage them in Admin → Agents. */
export function AgentsDirectory() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">AI Agents</h2>
          <p className="max-w-2xl text-sm text-muted-foreground">
            When a question spans departments, the assistant asks the agents that know each part and combines what they
            find. Every agent searches only what you can access.
          </p>
        </div>
        <Link href="/chat" className={buttonVariants({ variant: "outline" })}>
          <MessageSquareText data-icon="inline-start" />
          Ask the assistant
        </Link>
      </header>
      <SignedInGate purpose="see the agents">{(me) => <AgentGrid me={me} />}</SignedInGate>
    </div>
  );
}

function AgentGrid({ me }: { me: Me }) {
  const { state, refetch } = useApiQuery(workKeys.agents, workApi.listAgents);
  const canManage = me.org_role === "owner" || me.org_role === "admin";

  if (state.status === "loading") {
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true" aria-label="Loading agents">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-48 rounded-xl" />
        ))}
      </div>
    );
  }
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  if (state.data.length === 0) {
    return (
      <EmptyState
        icon={Bot}
        title="No agents yet"
        description="Every question is answered by one search across what you can access."
        action={
          canManage ? (
            <Link href="/admin/agents" className={buttonVariants({ variant: "outline", size: "sm" })}>
              Set up agents
            </Link>
          ) : undefined
        }
      />
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {state.data.map((agent) => (
          <li key={agent.key} className="flex flex-col gap-3 rounded-xl border bg-card p-5">
            <div className="flex items-center gap-3">
              <div className="flex size-9 items-center justify-center rounded-lg border bg-muted/40">
                <Bot className="size-4 text-muted-foreground" aria-hidden="true" />
              </div>
              <h3 className="font-medium">{agent.name}</h3>
            </div>
            <p className="text-sm text-muted-foreground">{agent.description}</p>
            {agent.examples.length > 0 ? (
              <div className="mt-auto flex flex-col gap-1.5">
                <p className="text-xs font-medium text-muted-foreground">For example</p>
                <ul className="flex flex-col gap-1">
                  {agent.examples.slice(0, 3).map((example) => (
                    <li key={example} className="rounded-lg bg-muted/40 px-3 py-1.5 text-sm">
                      “{example}”
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
      {canManage ? (
        <Link href="/admin/agents" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "w-fit text-muted-foreground")}>
          <Settings2 data-icon="inline-start" />
          Manage agents in Admin
        </Link>
      ) : null}
    </div>
  );
}
