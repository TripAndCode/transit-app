import { useEffect, useRef, useState, type RefObject } from "react";

/** Fraction of the target that must be on screen before it counts as "in
 *  view" -- high enough that a section barely peeking over the fold doesn't
 *  fire immediately on load. */
const VISIBLE_THRESHOLD = 0.15;

/**
 * True once the returned ref's element has scrolled into view. The caller's
 * own stylesheet is what actually makes a "not yet revealed" section hidden
 * (scoped to `prefers-reduced-motion: no-preference`), so a reduced-motion
 * viewer never sees a section start hidden regardless of what this hook
 * reports -- no reduced-motion check is needed here.
 *
 * Falls open (reveals on the next animation frame) in an environment with no
 * `IntersectionObserver` rather than leaving the section permanently hidden.
 */
export function useRevealOnScroll<T extends HTMLElement>(): [RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") {
      const raf = requestAnimationFrame(() => setRevealed(true));
      return () => cancelAnimationFrame(raf);
    }
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
