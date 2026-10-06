import { useSearchParams } from "react-router-dom";
import type { Agency } from "./types";
import { useAgencies } from "./hooks";
import { DEFAULT_RANGE_DAYS, isoDaysBefore } from "./scope";

/**
 * The agency's most recent `DEFAULT_RANGE_DAYS`-day window, ending at its
 * real latest data date: the target of the "Jump to the latest data"
 * recovery an empty view offers. It returns the window whenever the agency
 * has any data at all.
 */
export function latestDataWindow(
  agencyId: number | null,
  agencies: Agency[] | undefined,
): { from: string; to: string } | null {
  if (agencyId == null || !agencies) return null;
  const latestDataDate = agencies.find((a) => a.agency_id === agencyId)?.latest_data_date;
  if (!latestDataDate) return null;
  return { from: isoDaysBefore(latestDataDate, DEFAULT_RANGE_DAYS - 1), to: latestDataDate };
}

/**
 * A recovery callback that overwrites the URL's from/to with the agency's
 * latest-data window, leaving every other query param untouched. Returns
 * `null` (render no control) when the agency has no data to jump to.
 */
export function useJumpToLatestDataRange(agencyId: number | null): (() => void) | null {
  const { data: agencies } = useAgencies();
  const [, setParams] = useSearchParams();
  const range = latestDataWindow(agencyId, agencies);
  if (!range) return null;
  return () => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("from", range.from);
        next.set("to", range.to);
        return next;
      },
      { replace: true },
    );
  };
}
