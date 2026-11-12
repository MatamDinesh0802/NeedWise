"use client";

import { useSyncExternalStore } from "react";

const never = () => () => {};

/**
 * False on the server and while hydrating, true afterwards. For values that depend on the
 * browser, such as its time zone: rendering them on the server would give HTML the browser
 * disagrees with, and React keeps the server's attribute when they differ.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    never,
    () => true,
    () => false,
  );
}
