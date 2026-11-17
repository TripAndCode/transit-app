import type { SlowZoneSegmentData, SlowZonesResponse } from "./slowZonesApi";

export function segmentKey(s: { from_stop_id: string; to_stop_id: string }): string {
  return `${s.from_stop_id}\u0000${s.to_stop_id}`;
}

/** The newest week's excess over the timetable, in seconds, by class: the
 *  map deepens one colour by class, so no class reads as an alarm. A
 *  segment observed too little that week has no class. */
export const EXCESS_CLASS_EDGES_SEC = [0, 30, 60, 120] as const;

export function excessClass(segment: SlowZoneSegmentData): number | null {
  const latest = segment.latest;
  if (latest == null || !latest.enough_samples || latest.excess_sec == null) return null;
  const excess = latest.excess_sec;
  return EXCESS_CLASS_EDGES_SEC.filter((edge) => excess > edge).length;
}

type SegmentProps = { key: string; excess_class: number; slow: boolean };

/** One straight line per segment whose two stops have a position, which is
 *  also the line the speed is measured along. */
export function segmentFeatures(data: SlowZonesResponse | undefined): GeoJSON.FeatureCollection<GeoJSON.LineString, SegmentProps> {
  const slow = new Set((data?.slow_zones ?? []).map(([from, to]) => segmentKey({ from_stop_id: from, to_stop_id: to })));
  const features = (data?.segments ?? []).flatMap((s) => {
    if (s.from_lon == null || s.from_lat == null || s.to_lon == null || s.to_lat == null) return [];
    const key = segmentKey(s);
    return [
      {
        type: "Feature" as const,
        geometry: {
          type: "LineString" as const,
          coordinates: [
            [s.from_lon, s.from_lat],
            [s.to_lon, s.to_lat],
          ],
        },
        // -1 for a segment with no class: drawn faintest.
        properties: { key, excess_class: excessClass(s) ?? -1, slow: slow.has(key) },
      },
    ];
  });
  return { type: "FeatureCollection", features };
}

/** [west, south, east, north] over every placed segment, null with none. */
export function segmentBounds(segments: SlowZoneSegmentData[]): [number, number, number, number] | null {
  let bounds: [number, number, number, number] | null = null;
  for (const s of segments) {
    for (const [lon, lat] of [
      [s.from_lon, s.from_lat],
      [s.to_lon, s.to_lat],
    ]) {
      if (lon == null || lat == null) continue;
      bounds = bounds
        ? [Math.min(bounds[0], lon), Math.min(bounds[1], lat), Math.max(bounds[2], lon), Math.max(bounds[3], lat)]
        : [lon, lat, lon, lat];
    }
  }
  return bounds;
}

/** The slow zones' segments, in the response's order (longest-standing first). */
export function slowZoneSegments(data: SlowZonesResponse | undefined): SlowZoneSegmentData[] {
  if (!data) return [];
  const byKey = new Map(data.segments.map((s) => [segmentKey(s), s]));
  return data.slow_zones.flatMap(([from, to]) => {
    const s = byKey.get(segmentKey({ from_stop_id: from, to_stop_id: to }));
    return s ? [s] : [];
  });
}

/** The observed weeks' excess, oldest first, for a sparkline. */
export function observedExcess(segment: SlowZoneSegmentData): number[] {
  return (segment.weekly_excess_sec ?? []).filter((v): v is number => v != null);
}

/** Whether the streak runs back to the first week read, so it may have
 *  started earlier. */
export function streakFillsWindow(segment: SlowZoneSegmentData, weeks: string[]): boolean {
  return weeks.length > 0 && segment.slow_weeks >= weeks.length;
}
