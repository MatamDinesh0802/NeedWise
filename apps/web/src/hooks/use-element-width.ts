"use client";

import { useEffect, useState, type RefObject } from "react";

/** The element's content width, kept current with a ResizeObserver. `initial` until measured. */
export function useElementWidth(ref: RefObject<HTMLElement | null>, initial = 640): number {
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const next = Math.floor(entries[0]?.contentRect.width ?? 0);
      if (next > 0) setWidth(next);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
