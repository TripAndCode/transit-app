import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { apiGet } from "../../api/client";
import type { CoverageGapReason } from "../../api/types";

/** api.routers.segments.TimeBand: the stored bands, without "all". */
export const SLOW_ZONE_BANDS = ["morning", "forenoon", "noon", "afternoon", "evening", "night", "late_night"] as const;
export type SlowZoneBand = (typeof SLOW_ZONE_BANDS)[number];
export const SLOW_ZONE_DAY_TYPES = ["weekday", "weekend_holiday"] as const;
export type SlowZoneDayType = (typeof SLOW_ZONE_DAY_TYPES)[number];

export interface SegmentWeek {
  samples: number;
  days: number;
  run_p50_sec: number | null;
  run_p85_sec: number | null;
  scheduled_run_sec: number | null;
  excess_sec: number | null;
  /** Over the straight-line distance between the two stops. */
  speed_kmh: number | null;
  trips_per_day: number | null;
  minutes_lost_per_day: number | null;
  enough_samples: boolean;
}

export interface SlowZoneSegmentData {
  from_stop_id: string;
  from_stop_name: string | null;
  from_lon: number | null;
  from_lat: number | null;
  to_stop_id: string;
  to_stop_name: string | null;
  to_lon: number | null;
  to_lat: number | null;
  distance_m: number | null;
  routes: string[];
  latest: SegmentWeek | null;
  /** One per response week, oldest first; null for a week with too few runs. */
  weekly_excess_sec: (number | null)[];
  slow_weeks: number;
  slow_since: string | null;
}

export interface SlowZonesResponse {
  available: boolean;
  unavailable_reason: CoverageGapReason | null;
  last_checked: string | null;
  value_basis: "feed_prediction";
  day_type: SlowZoneDayType;
  time_band: SlowZoneBand;
  /** The Mondays of the weeks read, oldest first. */
  weeks: string[];
  thresholds: { excess_sec: number; min_scheduled_sec: number; min_weeks: number; min_samples: number };
  segments: SlowZoneSegmentData[];
  /** [from_stop_id, to_stop_id] of each slow zone, longest-standing first. */
  slow_zones: [string, string][];
}

export function slowZonesPath(agencyId: number, band: SlowZoneBand, dayType: SlowZoneDayType): string {
  return `/api/${agencyId}/segments/slow-zones?${new URLSearchParams({ time_band: band, day_type: dayType }).toString()}`;
}

export function useSlowZones(
  agencyId: number | null,
  band: SlowZoneBand,
  dayType: SlowZoneDayType,
): UseQueryResult<SlowZonesResponse> {
  return useQuery({
    queryKey: ["slow-zones", agencyId, band, dayType],
    queryFn: ({ signal }) => apiGet<SlowZonesResponse>(slowZonesPath(agencyId as number, band, dayType), { signal }),
    enabled: agencyId != null,
    staleTime: 5 * 60 * 1000,
    placeholderData: keepPreviousData,
  });
}
