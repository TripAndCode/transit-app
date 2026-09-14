import type { RouteShapeResponse, RouteShapeStop } from "../../api/types";

export function orderedStops(data: RouteShapeResponse): RouteShapeStop[] {
  return [...data.stops, ...(data.unobserved_stops ?? []).map((s) => ({ ...s, avg_min: null, samples: 0 }))]
    .sort((a, b) => a.stop_sequence - b.stop_sequence);
}

/** Match actual stop ids AND sequence, never a different stop at the same index. */
export function matchedPrevious(stop: RouteShapeStop, previous: RouteShapeStop[]): number | null {
  if (!stop.stop_id) return null;
  return previous.find((s) => s.stop_id === stop.stop_id && s.stop_sequence === stop.stop_sequence)?.avg_min ?? null;
}
