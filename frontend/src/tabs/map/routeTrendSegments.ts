import type { RouteShapeStop, UnobservedStop } from "../../api/types";

export interface TrendSegmentFeature {
  type: "Feature";
  geometry: { type: "LineString"; coordinates: [number, number][] };
  properties: { avg_min: number; has_data: boolean };
}

type MergedStop = {
  stop_sequence: number;
  lon: number;
  lat: number;
  avg_min: number;
  has_data: boolean;
};

/** Builds one 2-point LineString feature per consecutive stop pair (in
 *  stop_sequence order, merging observed + unobserved stops), so the route
 *  line can be colored per segment to show where delay accumulates along
 *  the route. Segment i is colored by the SECOND stop of the pair — the
 *  stop the bus is arriving at — so scanning the segments in travel
 *  direction reads as "delay observed by the time the bus reaches each
 *  successive stop." Segments leading into a stop with no observations
 *  get has_data: false / avg_min: 0, mirroring the existing has_data
 *  convention used for the stop dots in useRouteOverlay.ts. */
export function buildTrendSegments(
  stops: RouteShapeStop[],
  unobservedStops: UnobservedStop[],
): TrendSegmentFeature[] {
  const merged: MergedStop[] = [
    ...stops.map((s) => {
      const hasData = (s.samples ?? 0) > 0;
      return {
        stop_sequence: s.stop_sequence,
        lon: s.lon,
        lat: s.lat,
        avg_min: hasData ? (s.avg_min ?? 0) : 0,
        has_data: hasData,
      };
    }),
    ...unobservedStops.map((s) => ({
      stop_sequence: s.stop_sequence,
      lon: s.lon,
      lat: s.lat,
      avg_min: 0,
      has_data: false,
    })),
  ].sort((a, b) => a.stop_sequence - b.stop_sequence);

  const segments: TrendSegmentFeature[] = [];
  for (let i = 0; i < merged.length - 1; i++) {
    const from = merged[i];
    const to = merged[i + 1];
    segments.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[from.lon, from.lat], [to.lon, to.lat]] },
      properties: { avg_min: to.avg_min, has_data: to.has_data },
    });
  }
  return segments;
}
