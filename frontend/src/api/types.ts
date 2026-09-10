export type Agency = {
  agency_id: number;
  agency_name: string;
  feed_url: string;
  static_url: string | null;
  latest_data_date: string | null;
};

export type RouteBucket = "anomaly" | "watch" | "normal" | "no_baseline";

export type RouteSummary = {
  route_code: string;
  service_type: string | null;
  avg_delay_sec: number;
  worst_delay_sec: number;
  trips_observed: number;
  samples: number;
  last_seen_at: string | null;
  baseline_avg_sec: number | null;
  baseline_p90_sec: number | null;
  // Sample count backing the baseline itself (agg_route_stats), independent
  // of `samples` (today's count) and of `low_confidence` (which judges only
  // today's count) — null when there is no baseline at all.
  baseline_samples: number | null;
  deviation_sec: number | null;
  bucket: RouteBucket;
  low_confidence: boolean;
  has_baseline: boolean;
  late5_pct?: number | null;
};

export type RouteTrip = {
  trip_id: string;
  scheduled_time: string | null;
  headsign: string | null;
  avg_delay_sec: number;
  samples: number;
};

export type RouteTripsResponse = {
  date: string | null;
  trips: RouteTrip[];
};

export type RouteStopProfileRow = {
  stop_sequence: number;
  stop_id?: string | null;
  stop_name: string | null;
  avg_delay_sec: number;
  samples: number;
  cohort_avg_delay_sec?: number | null;
  cohort_route_count?: number;
  /** Total observation count backing `cohort_avg_delay_sec` (pooled across
   *  every route in the cohort) — distinct from `cohort_route_count`, which
   *  only counts how many DISTINCT routes contributed. */
  cohort_samples?: number;
  /** True when `cohort_samples` is too thin to trust `cohort_avg_delay_sec`
   *  (a lower floor than the route-level LOW_CONFIDENCE_SAMPLES — see
   *  api.triage.COHORT_LOW_CONFIDENCE_SAMPLES). Independent of `is_outlier`,
   *  which only gates on `cohort_route_count >= 2`. */
  cohort_low_confidence?: boolean;
  is_outlier?: boolean;
};

export type RouteStopProfileResponse = {
  date: string | null;
  stops: RouteStopProfileRow[];
};

export type RouteSummaryResponse = {
  latest_captured_at: string | null;
  date: string | null;
  routes: RouteSummary[];
  /** Feed-health for the latest analyzed date: raw observations and how many
   * were implausible (frozen/stale-feed delay spikes) and clamped out. */
  raw_samples: number;
  clamp_count: number;
};

export type RouteShapeStop = {
  stop_sequence: number;
  stop_name: string;
  /** Optional GTFS identifiers — surfaced in the unified map tooltip
   *  (when present) so route mode shows the same fields as heatmap mode. */
  stop_id?: string | null;
  stop_code?: string | null;
  platform_code?: string | null;
  lon: number;
  lat: number;
  avg_min: number | null;
  samples: number;
};

/** Stop on the chosen shape with no observations in the current window —
 *  rendered as a hollow marker so the route topology stays visible. */
export type UnobservedStop = {
  stop_sequence: number;
  stop_name: string;
  stop_id?: string | null;
  stop_code?: string | null;
  platform_code?: string | null;
  lon: number;
  lat: number;
};

export type RouteShapeResponse = {
  route: string;
  /**
   * Real road geometry from GTFS shapes when loaded; null otherwise.
   * LineString when the route has one observed shape variant (系統);
   * MultiLineString when it has several (e.g. an express bus with multiple
   * stopping patterns) — every observed variant renders, not just the
   * most-frequent one. useRouteOverlay flattens coords for bounds-fitting.
   */
  geometry: GeoJSON.LineString | GeoJSON.MultiLineString | null;
  stops: RouteShapeStop[];
  /** Stops on the chosen shape with no delay observations yet. Optional
   *  for back-compat with cached responses. */
  unobserved_stops?: UnobservedStop[];
};

export type HeatmapProps = {
  stop_id: string;
  stop_name: string;
  /** Optional GTFS stop_code (e.g. "②のりば"). Populated when the agency's // i18n-ignore: GTFS format example
   *  static feed includes it; clustered stops yield a "/-joined" set. */
  stop_code?: string;
  /** Optional GTFS platform_code (pole number, e.g. "2"). */
  platform_code?: string;
  avg_delay_min: number;
  p90_delay_min?: number | null;
  samples: number;
  /** Comma-joined list of route_codes contributing to this stop's avg.
   *  Optional because clients with cached responses from before the
   *  field was added will still parse correctly. */
  route_codes?: string;
  /** True when `samples` is too thin to trust `avg_delay_min`/`p90_delay_min`
   *  at full visual weight (same LOW_CONFIDENCE_SAMPLES floor as the route
   *  baselines elsewhere). Optional for the same cached-response reason as
   *  `route_codes`. */
  low_confidence?: boolean;
};

export type HeatmapCollection = GeoJSON.FeatureCollection<GeoJSON.Point, HeatmapProps> & {
  ctx?: ResponseCtx;
};

export type PeakHourBreakdownRoute = {
  route_code: string;
  service_type: string;
  avg_min: number;
  samples: number;
};

export type PeakHourBreakdown = {
  hour: number;
  dow: number | null;
  routes: PeakHourBreakdownRoute[];
};

export type ResponseCtx = {
  from: string;
  to: string;
  dow: string;
  time_band: string;
};

export type ReportMeta = {
  report_type: string;
  rendered_at: string;
};

/** Which on-time/late tolerance (and the shared dedup/exclusion rule) a
 *  report or comparison view's numbers actually used -- always present, but
 *  `preset`/`early_tolerance_sec`/`late_tolerance_sec` are `null` for report
 *  types with no on-time/late tolerance concept (e.g. `ranking`, `trend`).
 *  See pipeline/reports/definition.py, the single source of truth these
 *  values are resolved from. */
export type DefinitionMeta = {
  preset: string | null;
  early_tolerance_sec: number | null;
  late_tolerance_sec: number | null;
  exclusion_threshold_sec: number;
  measurement_point: string;
  dedup_rule: string;
};

export type ReportResponse = ReportMeta & {
  text: string;
  rows: unknown[];
  ctx?: ResponseCtx;
  definition: DefinitionMeta;
};

/** One high-frequency route's pooled Excess Waiting Time / coefficient of
 *  variation / long-gap rate over the request's range (item 94) -- see
 *  pipeline/reports/headway_quality.py's compute_headway_quality. A
 *  non-high-frequency route never appears in this list at all. */
export type HeadwayQualityRow = {
  route_code: string;
  ewt_sec: number | null;
  cov: number | null;
  long_gap_rate: number | null;
  samples: number;
};

export type HeadwayQualityResponse = {
  rows: HeadwayQualityRow[];
  ctx: ResponseCtx;
};

/** One configured per-route "minimum performance standard" (item 104),
 *  joined against the current actual value of its `metric_type` -- see
 *  pipeline/reports/performance_standard.py's compute_performance_standards
 *  for the achievement-rate / bonus-or-deduction formula. This is an
 *  INTERNAL SIMULATION ONLY -- see PerformanceStandardsResponse.disclaimer,
 *  which must always be rendered alongside these figures.
 *
 *  `actual_value`/`achievement_rate`/`estimated_bonus_deduction` are always
 *  null together (insufficient data, or a zero threshold_value making the
 *  ratio undefined). `metric_scope` is `"agency"` for
 *  `vehicle_km_delivered_pct` (no per-route breakdown exists -- every route
 *  configured with it reads its own agency's rate) and `"route"` for
 *  `ewt_sec`. */
export type PerformanceStandardRow = {
  route_code: string;
  metric_type: "ewt_sec" | "vehicle_km_delivered_pct";
  metric_scope: "route" | "agency";
  threshold_value: number;
  bonus_malus_rate: number;
  actual_value: number | null;
  achievement_rate: number | null;
  estimated_bonus_deduction: number | null;
};

export type PerformanceStandardsResponse = {
  rows: PerformanceStandardRow[];
  ctx: ResponseCtx;
  disclaimer: string;
};

export type Suggestion = {
  report_type: string;
  route_code: string;
  reason_text: string;
  severity: "notable" | "normal";
  // The evaluation window the rule that produced this suggestion actually
  // used (ISO date strings) -- pin click-through navigation to this window
  // rather than the user's ambient Analysis tab filter.
  from_date: string;
  to_date: string;
};

export type TrendDay = {
  date: string;
  avg_min: number;
  /** Trailing sample-weighted mean over the last (up to 7) OBSERVED days
   *  ending at this one — sits alongside `avg_min` (not a replacement) to
   *  make a low-traffic route's noisy day-to-day figure easier to read.
   *  `null` for week/month-bucketed series (already smoothed by width) and
   *  for cached responses from before this field existed. */
  avg_min_smoothed?: number | null;
  samples: number;
  top_offenders: { route_code: string; service_type: string; avg_min: number; samples: number }[];
};

/** ISO dates (within the trend's requested range) where the static feed
 *  version running that day changed from the previous calendar day's --
 *  see pipeline.reports.schedule_revision. Rendered as a boundary marker on
 *  the Trend chart so a metric shift there isn't misread as a
 *  service-quality change. */
export type RevisionBoundaries = string[];

export type DwellRunRoute = {
  route_code: string;
  service_type: string | null;
  dwell_samples: number;
  dwell_avg_sec: number | null;
  dwell_p50_sec: number | null;
  dwell_p90_sec: number | null;
  run_samples: number;
  run_avg_sec: number | null;
  run_p50_sec: number | null;
  run_p90_sec: number | null;
};

/** Payload for the ``dwell_run`` report -- see
 *  pipeline/reports/dwell_run.py's compute_dwell_run_decomposition, the
 *  single source of truth for this shape. `available=false` means this
 *  agency's feed never reports arrival delay (dwell/running time can't be
 *  derived at all); `time_band_supported=false` means the current time-band
 *  filter isn't servable by this decomposition yet -- both are explicit
 *  states the UI must render, never a silently empty/zero table. */
export type DwellRunPayload = {
  available: boolean;
  time_band_supported: boolean;
  routes: DwellRunRoute[];
};

export type ToolResult = {
  kind: "table" | "series" | "kv" | "empty" | "text";
  /** Backend-rendered summary string, already in the locale the
   *  current request asked for via Accept-Language. */
  summary: string;
  rows?: unknown[][];
  columns?: string[];
  series?: TrendDay[];
  pairs?: [string, unknown][];
};

export type AskResponse = {
  answer: string;
  tool_call: { name: string; arguments: Record<string, unknown> } | null;
  result: ToolResult | null;
  ctx: ResponseCtx;
  // Canonical intent fields — null when ASK_INTENT_CACHE_ENABLED is off
  signature_hash?: string | null;
  confidence?: number | null;
  canonical_args?: Record<string, unknown> | null;
  cache_outcome?: CacheOutcome | null;
};

// Canonical intent + guided UX

export type CacheOutcome = "hit" | "miss" | "bypass";

export type FilterCtx = {
  dow?: "all" | "weekday" | "weekend";
  time_band?: string;
  service?: string;
  from_date?: string;
  to_date?: string;
  routes?: string[];
  _client_id?: string;            // server adds this for migrated anon threads; client never sets it
};

export type Conversation = {
  conversation_id: string;        // UUID
  user_id: number | null;
  agency_id: number;
  title: string;
  filter_ctx: FilterCtx;
  pinned: boolean;
  created_at: string;
  updated_at: string;
};

export type ConvMessage = {
  message_id: number;
  conversation_id: string;
  role: "user" | "assistant";
  chip_id: string | null;
  tool: string | null;
  args: Record<string, unknown> | null;
  signature_hash: string | null;
  result: {
    kind: string;
    summary: string | null;
    rows: unknown[] | null;
    columns: string[] | null;
    series: unknown | null;
    pairs: unknown | null;
  } | null;
  rendered_summary: string | null;
  created_at: string;
};

export type AppendMessageResult = { user: ConvMessage; assistant: ConvMessage };

// Anonymous (localStorage) shape — mirrors a Conversation + inline messages
export type AnonThread = {
  client_id: string;
  agency_id: number;
  title: string;
  filter_ctx: FilterCtx;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  messages: ConvMessage[];        // capped at 20 per thread
};

export type Route = {
  route_id: string;
  route_short_name: string | null;
  route_long_name: string | null;
  route_code: string | null;
  trip_headsigns: string[];
};

export interface ForecastHeatmapCell {
  dow: number; // 1=Mon .. 7=Sun (ISODOW)
  hour: number; // 0..23
  expected_avg_min: number | null;
  samples: number;
  low_confidence: boolean;
}

export interface ForecastHeatmap {
  route: string;
  cells: ForecastHeatmapCell[]; // always 168 (7×24)
  disclaimer: string;
}

// Mirrors api/triage.py::LOW_CONFIDENCE_SAMPLES — a bucket with fewer measurements
// is flagged low-confidence. Kept here so client-side banding (collapseToBands)
// uses the same threshold as the server, not a bare literal.
export const LOW_CONFIDENCE_SAMPLES = 30;

// Band keys/order mirror pipeline/reports/forecast.py::BANDS (display order).
export const BAND_ORDER = ["early", "morning", "midday", "evening", "night"] as const;
export type Band = (typeof BAND_ORDER)[number];

// Hour→band boundaries, identical to the server BANDS ranges. Used to collapse
// the per-route 7×24 heatmap into bands client-side (the agency grid arrives
// pre-banded from the server).
const BAND_RANGES: readonly [Band, number, number][] = [
  ["early", 0, 6],
  ["morning", 6, 9],
  ["midday", 9, 16],
  ["evening", 16, 19],
  ["night", 19, 24],
];

export function bandOf(hour: number): Band {
  const hit = BAND_RANGES.find(([, lo, hi]) => hour >= lo && hour < hi);
  return hit ? hit[0] : "night";
}

export interface ForecastOverviewGridCell {
  dow: number; // 1=Mon .. 7=Sun (ISODOW)
  band: Band;
  expected_avg_min: number | null;
  samples: number;
  low_confidence: boolean;
}

export interface ForecastOverviewWorst {
  dow: number;
  band: Band;
  expected_avg_min: number;
  samples: number;
}

export interface ForecastOverviewRoute {
  route_code: string;
  route_name: string;
  expected_avg_min: number;
  samples: number;
  low_confidence: boolean;
  /** Last 7 analyzed calendar days' avg delay, oldest first. Optional —
   *  absent in older cached responses or test fixtures; empty array when
   *  the route has no recent agg_route_daily rows. */
  recent_daily?: number[];
}

export interface ForecastOverview {
  grid: ForecastOverviewGridCell[]; // always 35 (7×5 bands)
  worst: ForecastOverviewWorst | null;
  routes: ForecastOverviewRoute[];
  disclaimer: string;
}

export type OverviewHeadline = {
  avg_min: number | null;
  baseline_avg_min: number | null;
  delta_min: number | null;
  delta_pct: number | null;
  samples: number;
  /** ISO date of the start of the 7-day window the headline covers
   *  (always anchored at ctx.to and 7 days wide, regardless of the
   *  full ctx range). Use this for the eyebrow label. */
  window_from: string;
  /** ISO date of the end of the headline 7-day window (anchored at the
   *  latest date that has data inside ctx, not necessarily ctx.to). */
  window_to: string;
};

export type OverviewMover = {
  route_code: string;
  route_short_name: string | null;
  delta_min: number;
  /** Null when the previous-window average is too close to zero for a
   *  percentage change to be a meaningful figure (see backend). */
  delta_pct: number | null;
  /** Avg delay (min) in the current 7-day window. */
  current_avg_min: number;
  /** Avg delay (min) in the prior 7-day window. */
  previous_avg_min: number;
  streak_weeks: number;
  sparkline_points: number[];
};

export type OverviewMovers = {
  worse: OverviewMover[];
  better: OverviewMover[];
};

export type OverviewConcentrationTopRoute = {
  route_code: string;
  route_short_name: string | null;
  share_pct: number;
};

export type OverviewConcentration = {
  top_routes: OverviewConcentrationTopRoute[];
  rest_share_pct: number;
  rest_route_count?: number;
};

export type OverviewTopDelayedRoute = {
  route_code: string;
  route_short_name: string | null;
  avg_min: number;
};

export type OverviewTopDelayed = {
  routes: OverviewTopDelayedRoute[];
  delayed_count: number;
};

export type OverviewPeakHour = {
  by_hour: (number | null)[];
  peak_hour: number;
  peak_avg_min: number;
};

export type OverviewServiceSplitDay = {
  date: string;
  weekday: number | null;
  weekend: number | null;
};

export type OverviewSummary = {
  headline: OverviewHeadline;
  movers: OverviewMovers;
  concentration: OverviewConcentration;
  top_delayed: OverviewTopDelayed;
  peak_hour: OverviewPeakHour | null;
  /** Weekday-only 24-hour profile, used by the peak-hour modal split. */
  peak_hour_weekday?: OverviewPeakHour | null;
  /** Weekend-only 24-hour profile, used by the peak-hour modal split. */
  peak_hour_weekend?: OverviewPeakHour | null;
  service_split: Record<string, number>;
  /** Per-date weekday/weekend split, used by the service-split modal. */
  service_split_daily?: OverviewServiceSplitDay[];
  sparkline_points: number[];
};

export type NetworkAgencyRow = {
  agency_id: number;
  agency_name: string;
  avg_delay_min: number | null;
  on_time_pct: number | null;
  samples: number;
  raw_samples: number;
  clamp_count: number;
  clamp_pct: number | null;
  is_stale: boolean;
  data_from: string | null;
  data_to: string | null;
  planned_trips: number;
  executed_trips: number | null;
  service_delivered_pct: number | null;
  /** False whenever this agency has no manually-configured ridership weights
   * at all -- the weighted-view toggle must key off this, not off
   * weighted_on_time_pct being null (a configured agency with zero samples
   * in range is also null there, but is still configured). */
  has_ridership_weights: boolean;
  weighted_on_time_pct: number | null;
  /** The currently-loaded static-feed version this agency's headline supply
   *  figures below describe -- null when no static_version_id has been
   *  recorded for this agency yet (see pipeline.reports.supply). */
  static_version_id: string | null;
  /** Trips defined by the CURRENT static schedule (one full run of it), not
   *  a date-range total -- always present once any static schedule has ever
   *  been analyzed for this agency, independent of planned_vehicle_km. */
  planned_trip_count: number | null;
  /** Vehicle-km one full run of the current static schedule represents;
   *  null when this agency has no shapes.txt loaded (never 0). */
  planned_vehicle_km: number | null;
  /** "Vehicle-km delivered" rate -- null (fall back to planned_trip_count
   *  alone) whenever planned_vehicle_km or service_delivered_pct isn't
   *  available. */
  vehicle_km_delivered_pct: number | null;
};

export type NetworkSummary = {
  from: string;
  to: string;
  agencies: NetworkAgencyRow[];
  definition: DefinitionMeta;
};
