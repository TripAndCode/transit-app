import { useEffect, useState, type RefObject } from "react";

type UseInViewOptions = {
  /** Passed straight to `IntersectionObserver`'s `rootMargin`. A negative
   *  inset (the default) triggers a little before the element reaches the
   *  viewport edge. */
  rootMargin?: string;
};

/**
 * `true` once the observed element has scrolled within `rootMargin` of the
 * viewport, and stays `true` from then on -- a one-shot progressive-reveal
 * trigger, not a live visibility tracker (a section that has revealed once
 * should not re-hide when scrolled past).
 *
 * Starts (and stays) `true` when `IntersectionObserver` doesn't exist, so a
 * section gated on this hook is never silently stuck in its pre-reveal
 * state. Pair with a transform-only CSS treatment that is already visible at
 * rest (never `opacity: 0`), so the section reads correctly even in that
 * fallback case or before the observer's first callback fires.
 */
export function useInView<T extends Element>(
  ref: RefObject<T | null>,
  { rootMargin = "-8%" }: UseInViewOptions = {},
): boolean {
  const [inView, setInView] = useState(() => typeof IntersectionObserver === "undefined");

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, rootMargin]);

  return inView;
}
