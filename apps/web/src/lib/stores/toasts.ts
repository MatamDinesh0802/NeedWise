/** Tiny framework-free toast store (one per tab). */

export interface Toast {
  id: number;
  title: string;
  description?: string;
  tone: "info" | "error";
}

const DURATION_MS = 6000;
let toasts: readonly Toast[] = [];
let counter = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());

export const toastStore = {
  getSnapshot: () => toasts,
  getServerSnapshot: () => [] as readonly Toast[],
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  dismiss(id: number) {
    toasts = toasts.filter((t) => t.id !== id);
    emit();
  },
};

export function toast(input: Omit<Toast, "id" | "tone"> & { tone?: Toast["tone"] }): number {
  const id = ++counter;
  toasts = [...toasts, { tone: "info", ...input, id }];
  emit();
  if (typeof window !== "undefined") window.setTimeout(() => toastStore.dismiss(id), DURATION_MS);
  return id;
}
