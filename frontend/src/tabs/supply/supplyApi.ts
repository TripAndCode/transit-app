import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { apiGet } from "../../api/client";

export type SupplyDayType = "weekday" | "saturday" | "sunday_holiday";
export const SUPPLY_DAY_TYPES: readonly SupplyDayType[] = ["weekday", "saturday", "sunday_holiday"];

/** pipeline.reports.stop_supply.AgencyStatus: only "ok" has stops. */
type SupplyAgencyStatus = "ok" | "no_timetable" | "not_built" | "unknown_day";

/** An operator's stops as columns, one array per field, index i one stop. */
interface SupplyStopColumns {
  stop_id: string[];
  lat: (number | null)[];
  lon: (number | null)[];
  departures: number[];
  longest_gap_min: number[];
}

export interface SupplyAgency {
  agency_id: number;
  agency_name: string;
  status: SupplyAgencyStatus;
  static_version_id: string | null;
  typical_day: string | null;
  stops: SupplyStopColumns | null;
}

export interface SupplyMapResponse {
  date: string;
  day_type: SupplyDayType;
  from_hour: number;
  to_hour: number;
  agencies: SupplyAgency[];
}

/** One stop, opened: what the map leaves out. */
export interface SupplyStopDetail {
  agency_id: number;
  stop_id: string;
  stop_name: string | null;
  departures: number;
  per_hour: number;
  longest_gap_min: number;
  /** Service-day seconds: 24:30 is 88200. Null with no departure that day. */
  first_sec: number | null;
  last_sec: number | null;
}

export interface SupplyQuery {
  /** Empty for the server's today. */
  date: string;
  dayType: SupplyDayType;
  fromHour: number;
  toHour: number;
}

function supplyParams({ date, dayType, fromHour, toHour }: SupplyQuery): URLSearchParams {
  const params = new URLSearchParams({ day_type: dayType, from_hour: String(fromHour), to_hour: String(toHour) });
  if (date) params.set("date", date);
  return params;
}

export function supplyPath(query: SupplyQuery): string {
  return `/api/network/supply?${supplyParams(query).toString()}`;
}

export function supplyStopPath(query: SupplyQuery, agencyId: number, stopId: string): string {
  const params = supplyParams(query);
  params.set("agency_id", String(agencyId));
  params.set("stop_id", stopId);
  return `/api/network/supply/stop?${params.toString()}`;
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

export function useSupplyStop(
  query: SupplyQuery,
  stop: { agencyId: number; stopId: string } | null,
): UseQueryResult<SupplyStopDetail> {
  return useQuery({
    queryKey: ["network-supply-stop", query.date, query.dayType, query.fromHour, query.toHour, stop?.agencyId, stop?.stopId],
    queryFn: ({ signal }) =>
      stop
        ? apiGet<SupplyStopDetail>(supplyStopPath(query, stop.agencyId, stop.stopId), { signal })
        : Promise.reject(new Error("no stop opened")),
    staleTime: 5 * 60 * 1000,
    enabled: stop != null && query.toHour > query.fromHour,
  });
}
