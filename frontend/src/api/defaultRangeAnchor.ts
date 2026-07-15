import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useAgencies } from "./hooks";
import { DEFAULT_RANGE_DAYS, isoDaysAgo, isoDaysBefore } from "./rangeContext";

/**
 * On a fresh visit (no explicit from/to in the URL), rewrites the URL to
 * anchor the default 30-day window at the agency's real latest data date
 * instead of today, when today's default window would otherwise be
 * guaranteed empty. Never touches an explicit from/to already present, and
 * never fires for an agency with no aggregated data at all (its EmptyState
 * is already correct in that case).
 *
 * Reads the already-cached agencies list (useAgencies, 5min staleTime,
 * already fetched by AgencyPicker on nearly every page) — no new network
 * round-trip in the common case.
 */
export function useDefaultRangeAnchor(agencyId: number | null): void {
  const { data: agencies } = useAgencies();
  const [params, setParams] = useSearchParams();

  useEffect(() => {
    if (agencyId == null || !agencies) return;
    if (params.get("from") || params.get("to")) return;

    const agency = agencies.find((a) => a.agency_id === agencyId);
    const latestDataDate = agency?.latest_data_date;
    if (!latestDataDate) return;

    const windowStart = isoDaysAgo(DEFAULT_RANGE_DAYS - 1);
    if (latestDataDate >= windowStart) return; // already inside today's default window

    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("from", isoDaysBefore(latestDataDate, DEFAULT_RANGE_DAYS - 1));
        next.set("to", latestDataDate);
        return next;
      },
      { replace: true },
    );
  }, [agencyId, agencies, params, setParams]);
}
