"use client";

import { useApiQuery } from "@/hooks/use-api-query";
import { useSession } from "@/hooks/use-session";
import { editionApi, editionKey, type EditionInfo } from "@/lib/edition";

/** The deployment's edition (one shared request), fetched once someone is signed in. */
export function useEdition(): { edition: EditionInfo | null; loading: boolean; error: unknown; retry: () => void } {
  const { state: session } = useSession();
  const signedIn = session.kind === "signed_in";
  const { state, refetch } = useApiQuery(signedIn ? editionKey : null, () => editionApi.get());
  return {
    edition: state.status === "success" ? state.data : null,
    loading: session.kind === "loading" || (signedIn && state.status === "loading"),
    error: state.status === "error" ? state.error : null,
    retry: () => void refetch(),
  };
}
