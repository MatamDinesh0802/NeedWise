import { ShieldAlert, ShieldCheck } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { ChainResult } from "@/lib/admin/api";
import { verifyResultView } from "@/lib/admin/audit";
import { cn } from "@/lib/utils";

/** The outcome of "Verify integrity": green when intact, red with the first broken event. */
export function VerifyResultAlert({ result }: { result: ChainResult }) {
  const view = verifyResultView(result);
  return (
    <Alert
      variant={view.tone === "danger" ? "destructive" : "default"}
      data-tone={view.tone}
      className={cn("sm:max-w-md", view.tone === "ok" && "border-emerald-500/40 text-emerald-700 dark:text-emerald-400")}
    >
      {view.tone === "ok" ? <ShieldCheck aria-hidden="true" /> : <ShieldAlert aria-hidden="true" />}
      <AlertTitle>{view.title}</AlertTitle>
      <AlertDescription>{view.description}</AlertDescription>
    </Alert>
  );
}
