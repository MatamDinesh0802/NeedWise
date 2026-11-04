import { cn } from "@/lib/utils";

/** Name-agnostic product mark: three connected knowledge nodes. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      aria-hidden="true"
      className={cn("size-8 shrink-0", className)}
    >
      <rect width="32" height="32" rx="8" className="fill-primary" />
      <g className="stroke-primary-foreground" strokeWidth="2" strokeLinecap="round">
        <path d="M10 21 16 10l6 11" fill="none" />
        <path d="M10 21h12" fill="none" />
      </g>
      <g className="fill-primary-foreground">
        <circle cx="16" cy="10" r="3" />
        <circle cx="10" cy="21" r="3" />
        <circle cx="22" cy="21" r="3" />
      </g>
    </svg>
  );
}
