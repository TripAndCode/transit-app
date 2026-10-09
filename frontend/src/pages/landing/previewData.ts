// Static, deterministic fixtures for the landing page's scroll narrative
// (`ScrollNarrative.tsx`). None of this is fetched -- the narrative renders
// before sign-in, with no agency/session context to query real aggregates
// for, so every figure here is illustrative example data feeding the real
// chart components, not a snapshot of anything real.

import type { RouteShapeStop, TrendDay } from "../../api/types";
import type { StopEvidence } from "../../tabs/ask/stopEvidence";

// "Delay builds along a route" -- fed to the real `StopChart`. Delay climbs
// stop-by-stop toward the end of the line; the previous-period comparison
// stays comparatively flat, so the two lines visibly diverge as the route
// runs on, the same shape a real degrading route produces.
export const PREVIEW_ROUTE_STOPS: RouteShapeStop[] = [
  { stop_sequence: 1, stop_name: "Riverside Sta.", stop_id: "S1", lon: 139.70, lat: 35.66, avg_min: 0.3, samples: 240 },
  { stop_sequence: 2, stop_name: "Market St.", stop_id: "S2", lon: 139.706, lat: 35.663, avg_min: 0.6, samples: 238 },
  { stop_sequence: 3, stop_name: "City Hall", stop_id: "S3", lon: 139.712, lat: 35.667, avg_min: 1.4, samples: 231 },
  { stop_sequence: 4, stop_name: "University", stop_id: "S4", lon: 139.719, lat: 35.671, avg_min: 2.1, samples: 226 },
  { stop_sequence: 5, stop_name: "Harborview", stop_id: "S5", lon: 139.726, lat: 35.675, avg_min: 3.0, samples: 219 },
  { stop_sequence: 6, stop_name: "Pier Terminal", stop_id: "S6", lon: 139.733, lat: 35.679, avg_min: 3.9, samples: 214 },
];

export const PREVIEW_ROUTE_STOPS_PREVIOUS: RouteShapeStop[] = [
  { stop_sequence: 1, stop_name: "Riverside Sta.", stop_id: "S1", lon: 139.70, lat: 35.66, avg_min: 1.0, samples: 231 },
  { stop_sequence: 2, stop_name: "Market St.", stop_id: "S2", lon: 139.706, lat: 35.663, avg_min: 1.1, samples: 229 },
  { stop_sequence: 3, stop_name: "City Hall", stop_id: "S3", lon: 139.712, lat: 35.667, avg_min: 1.0, samples: 225 },
  { stop_sequence: 4, stop_name: "University", stop_id: "S4", lon: 139.719, lat: 35.671, avg_min: 1.2, samples: 220 },
  { stop_sequence: 5, stop_name: "Harborview", stop_id: "S5", lon: 139.726, lat: 35.675, avg_min: 1.1, samples: 213 },
  { stop_sequence: 6, stop_name: "Pier Terminal", stop_id: "S6", lon: 139.733, lat: 35.679, avg_min: 1.3, samples: 208 },
];

// "Every day, compared" -- fed to the real `DailyChart` (the same component
// Reports uses). A couple of rough days against a calmer trailing average,
// so the raw-vs-smoothed lines both have something to show.
export const PREVIEW_DAILY_TREND: TrendDay[] = [
  { date: "2026-08-25", avg_min: 1.8, avg_min_smoothed: 2.0, samples: 412, top_offenders: [] },
  { date: "2026-08-26", avg_min: 2.4, avg_min_smoothed: 2.1, samples: 405, top_offenders: [] },
  { date: "2026-08-27", avg_min: 1.6, avg_min_smoothed: 2.0, samples: 398, top_offenders: [] },
  { date: "2026-08-28", avg_min: 3.1, avg_min_smoothed: 2.2, samples: 420, top_offenders: [] },
  { date: "2026-08-29", avg_min: 2.9, avg_min_smoothed: 2.3, samples: 431, top_offenders: [] },
  { date: "2026-08-30", avg_min: 1.2, avg_min_smoothed: 2.1, samples: 260, top_offenders: [] },
  { date: "2026-08-31", avg_min: 1.0, avg_min_smoothed: 1.9, samples: 245, top_offenders: [] },
  { date: "2026-09-01", avg_min: 2.0, avg_min_smoothed: 1.9, samples: 400, top_offenders: [] },
  { date: "2026-09-02", avg_min: 2.6, avg_min_smoothed: 2.0, samples: 411, top_offenders: [] },
  { date: "2026-09-03", avg_min: 4.2, avg_min_smoothed: 2.4, samples: 418, top_offenders: [] },
  { date: "2026-09-04", avg_min: 3.0, avg_min_smoothed: 2.6, samples: 407, top_offenders: [] },
  { date: "2026-09-05", avg_min: 1.7, avg_min_smoothed: 2.4, samples: 397, top_offenders: [] },
  { date: "2026-09-06", avg_min: 1.1, avg_min_smoothed: 2.1, samples: 250, top_offenders: [] },
  { date: "2026-09-07", avg_min: 1.3, avg_min_smoothed: 1.9, samples: 244, top_offenders: [] },
];

// "Ask, get evidence" -- fed to the real `StopEvidenceChart`, the same
// evidence view a live Ask answer renders. One stop with no observations
// (`minutes: null`) so the fixture exercises the same missing-data path
// real data hits.
export const PREVIEW_ASK_EVIDENCE: StopEvidence[] = [
  { sequence: 1, name: "Riverside Sta.", minutes: 0.4, samples: 240 },
  { sequence: 2, name: "Market St.", minutes: 0.7, samples: 238 },
  { sequence: 3, name: "City Hall", minutes: 1.5, samples: 231 },
  { sequence: 4, name: "University", minutes: null, samples: 0 },
  { sequence: 5, name: "Harborview", minutes: 2.8, samples: 219 },
  { sequence: 6, name: "Pier Terminal", minutes: 3.6, samples: 214 },
];
