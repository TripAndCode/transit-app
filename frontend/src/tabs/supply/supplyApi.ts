import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { apiGet } from "../../api/client";

export type SupplyDayType = "weekday" | "saturday" | "sunday_holiday";
export const SUPPLY_DAY_TYPES: readonly SupplyDayType[] = ["weekday", "saturday", "sunday_holiday"];

/** pipeline.reports.stop_supply.AgencyStatus: only "ok" has stops. */
export type SupplyAgencyStatus = "ok" | "no_timetable" | "not_built" | "unknown_day";

export interface SupplyAgency {
  agency_id: number;
  agency_name: string;
  status: SupplyAgencyStatus;
  static_version_id: string | null;
  typical_day: string | null;
}

export interface SupplyStop {
  agency_id: number;
  stop_id: string;
  stop_name: string | null;
  lat: number | null;
  lon: number | null;
  departures: number;
  per_hour: number;
  longest_gap_min: number;
  /** Service-day seconds: 24:30 is 88200. Null with no departure that day. */
  first_sec: number | null;
  last_sec: number | null;
}

export interface SupplyMapResponse {
  date: string;
  day_type: SupplyDayType;
  from_hour: number;
  to_hour: number;
  agencies: SupplyAgency[];
  stops: SupplyStop[];
}

export interface SupplyQuery {
  /** Empty for the server's today. */
  date: string;
  dayType: SupplyDayType;
  fromHour: number;
  toHour: number;
}

export function supplyPath({ date, dayType, fromHour, toHour }: SupplyQuery): string {
  const params = new URLSearchParams({ day_type: dayType, from_hour: String(fromHour), to_hour: String(toHour) });
  if (date) params.set("date", date);
  return `/api/network/supply?${params.toString()}`;
}

export function useNetworkSupply(query: SupplyQuery): UseQueryResult<SupplyMapResponse> {
  return useQuery({
    queryKey: ["network-supply", query.date, query.dayType, query.fromHour, query.toHour],
    queryFn: ({ signal }) => apiGet<SupplyMapResponse>(supplyPath(query), { signal }),
    staleTime: 5 * 60 * 1000,
    placeholderData: keepPreviousData,
    enabled: query.toHour > query.fromHour,
  });
}
