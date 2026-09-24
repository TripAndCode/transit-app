import { useRef, type CSSProperties, type ReactNode } from "react";
import { useInView } from "../../hooks/useInView";

/** Highest `--stagger` index a section can be given. A tab is one entrance
 *  group, not a queue: past this many steps the remaining sections share the
 *  last delay, so the whole tab still settles within
 *  `MAX_STAGGER * --dur-1` however many sections it grows. */
const MAX_STAGGER = 4;

type Props = {
  /** Position in the tab's single staggered entrance group, in render
   *  order. Sections enter one after another off this index rather than
   *  each running an animation of its own. */
  index?: number;
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
 *
 * The section's own children must not stack entrance animations of their
 * own: this wrapper is their entrance, and a bar that also grows or a
 * number that also fades inside it plays the same arrival twice.
 */
export function RevealSection({ index = 0, children }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref);

  return (
    <div
      ref={ref}
      className={`reveal${inView ? " reveal--in" : ""}`}
      style={{ "--stagger": Math.min(index, MAX_STAGGER) } as CSSProperties}
    >
      {children}
    </div>
  );
}
