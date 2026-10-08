import { createContext, useContext } from "react";

/** Which linked chart published the current focus. A chart never dims itself
 *  from its own hover: it already answers "which mark is this?" with a
 *  tooltip and an enlarged point, and blanking the chart under the pointer
 *  destroys the context the reader is reading. */
export type TrendFocusSource = "daily" | "hourly" | "dow";

/** The dimensions the linked trend charts can agree or disagree on. `dow` is
 *  ISO (1 = Monday … 7 = Sunday) — the same convention the forecast grid and
 *  the server's day-of-week aggregates use. */
export type TrendMark = { date?: string; dow?: number; hour?: number };

export type TrendFocus = TrendMark & { source: TrendFocusSource };

type SetTrendFocus = (focus: TrendFocus | null) => void;

/** The context carries only the publishing side. Reading the focus happens in
 *  CSS, off the provider wrapper's `data-focus-*` attributes, so a hover never
 *  re-renders a chart that is not under the pointer. */
export const TrendFocusCtx = createContext<SetTrendFocus>(() => {});

/** Charts reused outside a `TrendFocusProvider` (the forecast tab mounts the
 *  same band grid) get an inert setter rather than having to know whether
 *  they are linked to anything. */
export function useTrendFocus(): { setFocus: SetTrendFocus } {
  return { setFocus: useContext(TrendFocusCtx) };
}

/** Opacity a mark drops to while another chart's mark holds the focus; the
 *  value every generated rule writes into `--focus-dim`. Low enough to
 *  recede, high enough that the dimmed distribution is still readable as
 *  context. */
export const DIM_OPACITY = 0.18;

/** ISO weekday (1 = Monday … 7 = Sunday) of a `YYYY-MM-DD` date string.
 *  Parsed as UTC midnight so the host timezone can never shift the day. */
export function isoDow(dateISO: string): number {
  const utcDay = new Date(`${dateISO}T00:00:00Z`).getUTCDay();
  return utcDay === 0 ? 7 : utcDay;
}
