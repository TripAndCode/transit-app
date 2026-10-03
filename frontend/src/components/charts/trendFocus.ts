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

type TrendFocusApi = {
  focus: TrendFocus | null;
  setFocus: (focus: TrendFocus | null) => void;
};

const NO_FOCUS: TrendFocusApi = { focus: null, setFocus: () => {} };

export const TrendFocusCtx = createContext<TrendFocusApi>(NO_FOCUS);

/** Charts reused outside a `TrendFocusProvider` (the forecast tab mounts the
 *  same band grid) get an inert focus rather than having to know whether
 *  they are linked to anything. */
export function useTrendFocus(): TrendFocusApi {
  return useContext(TrendFocusCtx);
}

/** Opacity a mark drops to while another chart's mark holds the focus. Low
 *  enough to recede, high enough that the dimmed distribution is still
 *  readable as context. */
export const DIM_OPACITY = 0.18;

const DIMENSIONS = ["date", "dow", "hour"] as const;

/**
 * A mark dims when it disagrees with the focus on a dimension they both
 * carry. Dimensions only one side knows about are ignored, so hovering an
 * hour row — which has no date — narrows the daily chart by weekday alone
 * instead of blanking every day in it.
 */
export function isFocusDimmed(
  focus: TrendFocus | null,
  mark: TrendMark,
  viewer: TrendFocusSource,
): boolean {
  if (!focus || focus.source === viewer) return false;
  for (const key of DIMENSIONS) {
    const f = focus[key];
    const m = mark[key];
    if (f === undefined || m === undefined) continue;
    if (f !== m) return true;
  }
  return false;
}

/** ISO weekday (1 = Monday … 7 = Sunday) of a `YYYY-MM-DD` date string.
 *  Parsed as UTC midnight so the host timezone can never shift the day. */
export function isoDow(dateISO: string): number {
  const utcDay = new Date(`${dateISO}T00:00:00Z`).getUTCDay();
  return utcDay === 0 ? 7 : utcDay;
}
