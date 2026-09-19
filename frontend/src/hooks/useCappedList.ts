import { useState } from "react";

type CappedList<T> = {
  visible: T[];
  remaining: number;
  showMore: () => void;
};

/**
 * Caps a long list to `initialCap` items so the first render of a huge
 * table/queue stays cheap, then raises the cap by `initialCap` each time
 * `showMore` is called -- never dropping the tail outright, only deferring
 * its render. Mirrors the render-cap pattern already used by RoutesPicker's
 * own inline `visibleCap` state.
 */
export function useCappedList<T>(items: T[], initialCap: number): CappedList<T> {
  const [cap, setCap] = useState(initialCap);
  return {
    visible: items.slice(0, cap),
    remaining: Math.max(0, items.length - cap),
    showMore: () => setCap((c) => c + initialCap),
  };
}
