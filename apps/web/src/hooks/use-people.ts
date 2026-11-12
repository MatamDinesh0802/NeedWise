"use client";

import { useMemo } from "react";

import { useApiQuery } from "@/hooks/use-api-query";
import { useSession } from "@/hooks/use-session";
import { canUseDirectory, peopleApi, peopleKeys, type Person } from "@/lib/people";

/** Active colleagues (one shared request) as a list and a lookup by id. Pass false to skip it. */
export function usePeople(enabled: boolean) {
  const { state, refetch } = useApiQuery(enabled ? peopleKeys.all : null, () => peopleApi.list());
  const list: Person[] = useMemo(() => (state.status === "success" ? state.data : []), [state]);
  const byId = useMemo(() => new Map(list.map((p) => [p.id, p])), [list]);
  return { people: list, byId, loading: enabled && state.status === "loading", error: state.status === "error" ? state.error : null, refetch };
}

/** The directory for whoever is signed in: members get it, guests (and signed-out views) don't ask. */
export function useColleagues() {
  const { state } = useSession();
  const enabled = state.kind === "signed_in" && canUseDirectory(state.me.org_role);
  return { enabled, ...usePeople(enabled) };
}
