"use client";

import { ArrowLeft, BadgeCheck, ClipboardCheck, ExternalLink, ListTodo, Mail, Wrench, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { buttonVariants } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApprovalById, ApprovalStatusBadge } from "@/components/work/approval-card";
import { useApiQuery } from "@/hooks/use-api-query";
import { formatDateTime, formatRelative } from "@/lib/format";
import { canSeeAdministration, type Me } from "@/lib/session";
import { cn } from "@/lib/utils";
import { workApi, workKeys, type Approval } from "@/lib/work/api";
import { displayStatus, OFFICIAL_KIND, originLabel, toolTitle } from "@/lib/work/approvals";

const TOOL_ICONS: Record<string, LucideIcon> = { send_email: Mail, create_task: ListTodo, [OFFICIAL_KIND]: BadgeCheck };

export function ApprovalsInbox() {
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
      <header>
        <h2 className="text-xl font-semibold tracking-tight">Approvals</h2>
        <p className="text-sm text-muted-foreground">
          Actions the assistant and workflows want to take. Nothing is sent or changed until the approver says yes, and it then
          runs once, as the person who asked.
        </p>
      </header>
      <SignedInGate purpose="see approvals">{(me) => <Inbox me={me} />}</SignedInGate>
    </div>
  );
}

function Inbox({ me }: { me: Me }) {
  const pending = useApiQuery(workKeys.approvalList("pending"), () => workApi.listApprovals("pending"));
  const all = useApiQuery(workKeys.approvalList("all"), () => workApi.listApprovals("all"));

  const waiting = pending.state.status === "success" ? pending.state.data.filter((a) => a.approver_id === me.id && displayStatus(a) === "pending") : [];
  const requested = all.state.status === "success" ? all.state.data.filter((a) => a.requested_by === me.id) : [];
  const decided = all.state.status === "success" ? all.state.data.filter((a) => a.approver_id === me.id && a.status !== "pending") : [];

  return (
    <Tabs defaultValue="waiting" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <TabsList>
          <TabsTrigger value="waiting">
            Waiting for you
            {waiting.length > 0 ? (
              <span className="ml-1 rounded-full bg-amber-500/20 px-1.5 text-xs text-amber-800 tabular-nums dark:text-amber-300">{waiting.length}</span>
            ) : null}
          </TabsTrigger>
          <TabsTrigger value="requested">Requested by you</TabsTrigger>
          <TabsTrigger value="decided">Decided by you</TabsTrigger>
        </TabsList>
        <RefreshingHint active={(pending.state.status === "success" && pending.state.refreshing) || (all.state.status === "success" && all.state.refreshing)} />
      </div>
      <TabsContent value="waiting">
        <ApprovalList
          state={pending.state}
          items={waiting}
          onRetry={() => void pending.refetch()}
          empty={{ title: "Nothing is waiting for you", description: "When the assistant or a workflow proposes an action you need to approve, it shows up here." }}
        />
      </TabsContent>
      <TabsContent value="requested">
        <ApprovalList
          state={all.state}
          items={requested}
          onRetry={() => void all.refetch()}
          empty={{ title: "You haven't asked for anything yet", description: "In the assistant, choose “Research & act” and ask it to prepare an email or a task." }}
        />
      </TabsContent>
      <TabsContent value="decided">
        <ApprovalList
          state={all.state}
          items={decided}
          onRetry={() => void all.refetch()}
          empty={{ title: "No decisions yet", description: "Approvals you approved, edited or rejected are kept here." }}
        />
      </TabsContent>
    </Tabs>
  );
}

function ApprovalList({
  state,
  items,
  onRetry,
  empty,
}: {
  state: { status: "loading" | "success" | "error"; error?: unknown };
  items: Approval[];
  onRetry: () => void;
  empty: { title: string; description: string };
}) {
  if (state.status === "loading") return <TableSkeleton rows={3} />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={onRetry} />;
  if (items.length === 0) return <EmptyState icon={ClipboardCheck} title={empty.title} description={empty.description} />;
  return (
    <ul className="flex flex-col divide-y rounded-xl border">
      {items.map((approval) => {
        const Icon = TOOL_ICONS[approval.tool] ?? Wrench;
        const pending = displayStatus(approval) === "pending";
        return (
          <li key={approval.id}>
            <Link
              href={`/approvals/${approval.id}`}
              className="flex items-start gap-3 px-4 py-3 hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:outline-none"
            >
              <Icon className={cn("mt-0.5 size-4 shrink-0", pending ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground")} aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium break-words">{approval.summary}</p>
                <p className="text-xs text-muted-foreground">
                  {toolTitle(approval.tool)} · {originLabel(approval)} ·{" "}
                  <span title={formatDateTime(approval.created_at)}>{formatRelative(approval.created_at)}</span>
                  {pending ? <> · expires {formatRelative(approval.expires_at)}</> : null}
                </p>
              </div>
              <ApprovalStatusBadge approval={approval} />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/** /approvals/[id]: the same card, full page. Notification links point here. */
export function ApprovalPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <Link href="/approvals" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "w-fit")}>
        <ArrowLeft data-icon="inline-start" />
        Approvals
      </Link>
      <SignedInGate purpose="see this approval">{(me) => <ApprovalDetail id={id} me={me} />}</SignedInGate>
    </div>
  );
}

function ApprovalDetail({ id, me }: { id: string; me: Me }) {
  const { state } = useApiQuery(workKeys.approval(id), () => workApi.getApproval(id));
  const approval = state.status === "success" ? state.data : null;
  return (
    <>
      <ApprovalById id={id} meId={me.id} />
      {approval ? <OriginLinks approval={approval} me={me} /> : null}
    </>
  );
}

function OriginLinks({ approval, me }: { approval: Approval; me: Me }) {
  const conversation = approval.origin.conversation_id;
  const link = cn(buttonVariants({ variant: "outline", size: "sm" }), "w-fit");
  if (typeof conversation === "string" && approval.requested_by === me.id) {
    return (
      <Link href={`/chat/${conversation}`} className={link}>
        <ExternalLink data-icon="inline-start" />
        Open the conversation
      </Link>
    );
  }
  if (approval.origin.via === "workflow" && canSeeAdministration(me.org_role) && me.org_role !== "auditor") {
    return (
      <Link href="/admin/workflows" className={link}>
        <ExternalLink data-icon="inline-start" />
        Open workflow runs
      </Link>
    );
  }
  return null;
}
