import { useRef, type ReactNode } from "react";
import { useInView } from "../../hooks/useInView";

type Props = {
  children: ReactNode;
};

/**
 * Wraps one of the period-overview tab's collapsed-details sections
 * (peak-hour, concentration, service split) now that they render inline
 * instead of behind a `<details>` toggle. Content is always in the DOM and
 * fully visible at rest -- only a `.reveal` transform (never `opacity: 0`)
 * animates in once the section scrolls into view, so a viewer without JS,
 * with reduced motion, or ahead of the observer's first callback still sees
 * the real content immediately.
 */
export function RevealSection({ children }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref);

  return (
    <div ref={ref} className={`reveal${inView ? " reveal--in" : ""}`}>
      {children}
    </div>
  );
}
