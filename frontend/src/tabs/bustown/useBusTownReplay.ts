import { useQueries } from "@tanstack/react-query";
import { replayWindowQueryOptions, useReplayWindow } from "../../api/hooks";
import type { ReplayResponse } from "../../api/types";
import {
  SCRUBBER_END_HOUR,
  buildBusTownModel,
  desiredWindowKeys,
  mergeReplayWindows,
  scrubberStartHour,
  stopPairsFromWindows,
  stopPointsFromWindows,
  type BusTownModel,
  type ResolvedTrip,
  type StopPair,
  type StopPoint,
} from "./busTownModel";

const NO_WINDOWS: readonly ReplayResponse[] = [];

export type BusTownReplay = {
  loading: boolean;
  error: unknown;
  date: string | null;
  availableDates: string[];
  originHour: number | null;
  minSec: number;
  maxSec: number;
  model: BusTownModel;
  trips: ReadonlyMap<string, ResolvedTrip>;
  stopPoints: StopPoint[];
  stopPairs: StopPair[];
  routeNames: Readonly<Record<string, string>>;
};

/**
 * Loads one service day's replay windows and merges them into a drawable
 * model. `selectedDate` null defers to the endpoint's own default (newest
 * observed day); its response also names that day's own default window,
 * which anchors the window grid every other fetch here aligns to (see
 * `desiredWindowKeys`). That first window comes for free from the very same
 * call that discovers the day, so it is never re-requested through
 * `useQueries` below — only the neighbouring windows the playhead approaches
 * are.
 */
export function useBusTownReplay(
  agencyId: number | null,
  selectedDate: string | null,
  playheadSec: number,
  speed: number,
): BusTownReplay {
  const dayQuery = useReplayWindow(agencyId, selectedDate, null, null);
  const date = dayQuery.data?.date ?? null;
  const originHour = dayQuery.data?.from_hour ?? null;

  const desired = date != null && originHour != null ? desiredWindowKeys(date, originHour, playheadSec, speed) : [];
  const extra = desired.filter((key) => key.fromHour !== originHour);

  const extraResults = useQueries({
    queries: extra.map((key) => replayWindowQueryOptions(agencyId, key.date, key.fromHour, key.toHour)),
  });

  const windows: readonly ReplayResponse[] = dayQuery.data
    ? [dayQuery.data, ...extraResults.map((r) => r.data).filter((d): d is ReplayResponse => d != null)]
    : NO_WINDOWS;

  const trips = mergeReplayWindows(windows);
  const routeNames: Record<string, string> = {};
  for (const replayWindow of windows) {
    for (const route of replayWindow.routes) routeNames[route.code] = route.name;
  }

  return {
    loading: dayQuery.isLoading,
    error: dayQuery.data === undefined ? dayQuery.error : null,
    date,
    availableDates: dayQuery.data?.available_dates ?? [],
    originHour,
    minSec: scrubberStartHour(originHour) * 3600,
    maxSec: SCRUBBER_END_HOUR * 3600,
    model: buildBusTownModel(trips),
    trips,
    stopPoints: stopPointsFromWindows(windows),
    stopPairs: stopPairsFromWindows(windows),
    routeNames,
  };
}
