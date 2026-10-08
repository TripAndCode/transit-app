import { useEffect, useRef, useState, type RefObject } from "react";

/** Fraction of the target that must be on screen before it counts as "in
 *  view" -- high enough that a section barely peeking over the fold doesn't
 *  fire immediately on load. */
const VISIBLE_THRESHOLD = 0.15;

/**
 * True once the returned ref's element has scrolled into view. The caller's
 * stylesheet offsets a "not yet revealed" section downward and nothing more
 * -- it is never `opacity: 0`, so the content reads at full strength before
 * the observer's first callback, and a reduced-motion viewer (the offset is
 * scoped to `prefers-reduced-motion: no-preference`) never sees it move at
 * all. No reduced-motion check is needed here.
 *
 * Starts (and stays) true where there is no `IntersectionObserver`: with
 * nothing to clear the pending offset, the section must never enter it.
 */
export function useRevealOnScroll<T extends HTMLElement>(): [RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [revealed, setRevealed] = useState(() => typeof IntersectionObserver === "undefined");

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        setRevealed(true);
        observer.disconnect();
      },
      { threshold: VISIBLE_THRESHOLD },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref, revealed];
}
