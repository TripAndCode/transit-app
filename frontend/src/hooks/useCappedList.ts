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
 *
 * `resetKey` identifies *which* list is being shown. Without it the raised
 * cap outlives the data: after one "show more", switching report type or
 * agency renders the next list at the larger cap, and the guarantee this
 * hook exists for -- that the first render of a long list is cheap -- only
 * holds until the user interacts once.
 *
 * Reset happens during render rather than in an effect: React re-runs the
 * component immediately with the new state and never commits the stale one,
 * and `react-hooks/set-state-in-effect` is an error in this repo.
 */
export function useCappedList<T>(items: T[], initialCap: number, resetKey: unknown): CappedList<T> {
  const [state, setState] = useState({ cap: initialCap, key: resetKey });
  if (!Object.is(state.key, resetKey)) {
    setState({ cap: initialCap, key: resetKey });
  }
  const cap = Object.is(state.key, resetKey) ? state.cap : initialCap;
  return {
    visible: items.slice(0, cap),
    remaining: Math.max(0, items.length - cap),
    showMore: () => setState((s) => ({ ...s, cap: s.cap + initialCap })),
  };
}
