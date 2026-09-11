import type { LiveTrip, RouteSummary } from "../../api/types";

export function buildCurrentRouteSummaries(liveTrips: LiveTrip[], baselines: RouteSummary[]): RouteSummary[] {
  const tripsByRoute = new Map<string, LiveTrip[]>();
  for (const trip of liveTrips) {
    if (!trip.route_code) continue;
    const rows = tripsByRoute.get(trip.route_code) ?? [];
    rows.push(trip);
    tripsByRoute.set(trip.route_code, rows);
  }

  return [...tripsByRoute].map(([routeCode, trips]) => {
    const serviceType = trips[0].service_type;
    const routeBaselines = baselines.filter((route) => route.route_code === routeCode);
    const baseline = routeBaselines.find((route) => route.service_type === serviceType) ?? routeBaselines[0];
    const avgDelaySec = Math.round(trips.reduce((sum, trip) => sum + trip.dep_delay, 0) / trips.length);
    const worstDelaySec = Math.max(...trips.map((trip) => trip.dep_delay));
    const baselineAvgSec = baseline?.baseline_avg_sec ?? null;
    const baselineP90Sec = baseline?.baseline_p90_sec ?? null;
    const hasBaseline = baselineAvgSec != null && baselineP90Sec != null;
    const deviationSec = hasBaseline ? Math.round(avgDelaySec - baselineAvgSec) : null;

    let bucket: RouteSummary["bucket"];
    if (hasBaseline) {
      const midpoint = baselineAvgSec + (baselineP90Sec - baselineAvgSec) / 2;
      bucket = avgDelaySec > baselineP90Sec ? "anomaly" : avgDelaySec > midpoint ? "watch" : "normal";
    } else {
      bucket = avgDelaySec >= 300 ? "anomaly" : avgDelaySec >= 180 ? "watch" : "no_baseline";
    }

    return {
      route_code: routeCode,
      service_type: baseline?.service_type ?? serviceType,
      avg_delay_sec: avgDelaySec,
      worst_delay_sec: worstDelaySec,
      trips_observed: trips.length,
      samples: trips.length,
      last_seen_at: trips.reduce((latest, trip) => trip.captured_at > latest ? trip.captured_at : latest, trips[0].captured_at),
      baseline_avg_sec: baselineAvgSec,
      baseline_p90_sec: baselineP90Sec,
      baseline_samples: baseline?.baseline_samples ?? null,
      deviation_sec: deviationSec,
      bucket,
      low_confidence: trips.length < 2,
      has_baseline: hasBaseline,
      late5_pct: trips.filter((trip) => trip.dep_delay >= 300).length / trips.length * 100,
    };
  });
}
