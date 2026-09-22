import type { TimeBand } from "./rangeContext";

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

export type RouteSummaryResponse = {
  latest_captured_at: string | null;
  date: string | null;
  routes: RouteSummary[];
  /** Feed-health for the latest analyzed date: raw observations and how many
   * were implausible (frozen/stale-feed delay spikes) and clamped out. */
  raw_samples: number;
  clamp_count: number;
};

export type LiveTrip = {
  trip_id: string;
  route_code: string | null;
  service_type: string | null;
  scheduled_time: string | null;
  dep_delay: number;
  captured_at: string;
  stop_id: string | null;
  stop_sequence: number;
  stop_name: string | null;
  stop_lat: number | null;
  stop_lon: number | null;
  headsign: string | null;
  direction_id?: number | null;
};

export type LiveTripProgressStop = {
  stop_sequence: number;
  stop_id: string | null;
  stop_name: string | null;
  stop_lat: number | null;
  stop_lon: number | null;
  scheduled_time: string | null;
  dep_delay: number;
  reported_at: string;
};

export type LiveTripProgressResponse = {
  trip_id: string;
  route_code: string | null;
  headsign: string | null;
  direction_id: number | null;
  latest_captured_at: string | null;
  stops: LiveTripProgressStop[];
};

/** One stop's pooled delay inside one day-playback frame. `samples` counts
 *  observed trip visits to this stop in the bucket, not feed polls. */
export type TimelinePoint = {
  stop_id: string;
  stop_name: string | null;
  lon: number;
  lat: number;
  avg_delay_min: number;
  samples: number;
};

/** One time bucket of the service day. Frames are dense over 05:00–24:00, so
 *  an empty `points` is a statement about that hour, not a gap in the list;
 *  `mean_delay_min` is null exactly then. */
export type TimelineFrame = {
  t: string;
  points: TimelinePoint[];
  mean_delay_min: number | null;
  samples: number;
};

export type DelayTimelineResponse = {
  date: string;
  step_minutes: number;
  frames: TimelineFrame[];
};

export type LiveTripsResponse = {
  latest_captured_at: string | null;
  rows: LiveTrip[];
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
   * most-frequent one. Consumers flatten the coords for bounds-fitting.
   */
  geometry: GeoJSON.LineString | GeoJSON.MultiLineString | null;
  stops: RouteShapeStop[];
  /** Stops on the chosen shape with no delay observations yet. Optional
   *  for back-compat with cached responses. */
  unobserved_stops?: UnobservedStop[];
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

/** Every report `GET /api/{agency_id}/reports/{report_type}` serves --
 *  api/routers/reports.py's `_REPORT_TYPES` is the single source of truth,
 *  and the endpoint 404s on anything outside it. The Analysis tab lists
 *  `route_forecast` alongside these, but that one is served by /forecast and
 *  is deliberately not a member here. */
export type ReportType =
  | "ranking"
  | "ranking_best"
  | "on_time"
  | "worst_5min"
  | "trend"
  | "compare_ranking"
  | "dow_weekend"
  | "dow_weekday"
  | "dwell_run"
  | "council_summary"
  | "delay_certificate";

/** One observed stop of one trip, as a point on a time-distance diagram. */
export type RouteTripStop = {
  stop_id: string | null;
  stop_sequence: number;
  /** Seconds since the service day's 00:00, not a clock string: a GTFS
   *  post-midnight continuation (25:30) has no same-day "HH:MM" form, and a
   *  time axis needs a number anyway. Null when the row carries no usable
   *  scheduled time. */
  scheduled_sec: number | null;
  /** `scheduled_sec + delay_sec`; null whenever `scheduled_sec` is. */
  observed_sec: number | null;
  delay_sec: number;
};

export type RouteTrip = {
  trip_id: string;
  scheduled_time: string | null;
  headsign: string | null;
  avg_delay_sec: number;
  samples: number;
  /** Ordered by stop_sequence — this is the drawing order of the trip's
   *  polyline in the Marey diagram. */
  stops: RouteTripStop[];
};

export type RouteTripsResponse = {
  date: string | null;
  time_band: TimeBand;
  /** True when the route ran more trips than the endpoint will return and the
   *  least-delayed tail was dropped. */
  truncated: boolean;
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

export type ReportMeta = {
  report_type: ReportType;
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

/** A report cell the backend computes as a Python `Decimal` (chosen so its
 *  rounding matches Postgres `ROUND`'s half-up behaviour). `ReportResponse.
 *  rows` is an untyped `list` on the Python side, so a bare `Decimal` is
 *  serialised as a JSON *string* while the plain `int` columns beside it stay
 *  JSON numbers. Coerce with `Number()` before arithmetic or formatting. */
export type DecimalCell = number | string;

/** `ranking` and `ranking_best` -- same columns, opposite sort order.
 *  `p50_min`/`p90_min` are null when the merged histogram can't resolve a
 *  percentile; `avg_min` always resolves (the >20-sample gate). */
export type RankingRow = [
  route_code: string,
  service_type: string | null,
  avg_min: DecimalCell,
  p50_min: DecimalCell | null,
  p90_min: DecimalCell | null,
  samples: number,
];

/** `on_time`. The trailing `low_confidence` flag is appended by
 *  pipeline/stats.py's annotate_on_time_pct_confidence as a display-layer
 *  caveat (95% Wilson interval too wide to trust `on_time_pct`). */
export type OnTimeRow = [
  route_code: string,
  service_type: string | null,
  on_time_pct: DecimalCell,
  avg_min: DecimalCell,
  samples: number,
  low_confidence: boolean,
];

/** `worst_5min` -- routes ranked by count of severely-late observations. */
export type Worst5MinRow = [
  route_code: string,
  service_type: string | null,
  late5_count: number,
  avg_min: DecimalCell,
  samples: number,
];

/** `compare_ranking` -- per-route weekday-vs-weekend delay, sorted by the
 *  absolute difference. Carries no service_type: the comparison drops the
 *  service filter on purpose (a weekday-schedule service never runs on a
 *  weekend, so the pairing would always be empty). */
export type CompareRankingRow = [
  route_code: string,
  weekday_avg_min: DecimalCell,
  weekend_avg_min: DecimalCell,
  abs_delta_min: DecimalCell,
  signed_delta_min: DecimalCell,
];

/** `dow_weekday` and `dow_weekend`. `dow_label` is the backend's own
 *  Japanese group label for the half the rows were restricted to. */
export type DowRankingRow = [
  route_code: string,
  service_type: string | null,
  dow_label: string,
  avg_min: DecimalCell,
  samples: number,
];

/** `council_summary` -- exactly one agency-wide row.
 *  `on_time_pct`/`avg_delay_min` are null together when nothing matched the
 *  range; `executed_trips`/`service_delivered_pct` are null together when the
 *  delivered ratio isn't computable, which is never the same as zero. */
export type CouncilSummaryRow = [
  on_time_pct: number | null,
  avg_delay_min: number | null,
  samples: number,
  planned_trips: number,
  executed_trips: number | null,
  service_delivered_pct: number | null,
];

/** `delay_certificate` -- one row per physical trip-run exceeding the
 *  threshold. `actual_time` is `scheduled_time` shifted by `dep_delay_sec`,
 *  and may carry a day-boundary suffix rather than being a bare clock time. */
export type DelayCertificateRow = [
  agency_name: string,
  route_code: string,
  service_type: string | null,
  date: string,
  scheduled_time: string,
  actual_time: string,
  dep_delay_sec: number,
];

/** One hour-of-day × date cell of the trend heatmap. `sum_delay_sec` is the
 *  cell's exact raw-seconds total, null until the aggregate row has been
 *  rebuilt since the column was introduced -- only a caller pooling several
 *  cells needs it. */
export type TrendHourlyCell = {
  date: string;
  hour: number;
  avg_min: number | null;
  samples: number;
  sum_delay_sec?: number | null;
};

/** The dow × band grid the trend report reuses from the forecast summariser,
 *  minus the forecast-specific route ranking and disclaimer. */
export type TrendDowBand = {
  grid: ForecastOverviewGridCell[];
  worst: ForecastOverviewWorst | null;
};

/** The `trend` report's single structured row -- unlike every other report,
 *  `rows` here is one object, not a list of tuples. */
export type TrendPayload = {
  days: TrendDay[];
  hourly: TrendHourlyCell[];
  dow_band: TrendDowBand;
  /** Optional for back-compat with responses cached before the field existed;
   *  the endpoint always sends it (empty when the agency has no coverage). */
  revision_boundaries?: RevisionBoundaries;
};

type ReportEnvelope<T extends ReportType, Row> = {
  report_type: T;
  rendered_at: string;
  text: string;
  rows: Row[];
  ctx?: ResponseCtx;
  definition: DefinitionMeta;
};

/** Discriminated on `report_type`: each report's `rows` element type is
 *  derived from what api/routers/reports.py actually returns for it, so a
 *  consumer narrows on `report_type` instead of casting `rows` blind. */
export type ReportResponse =
  | ReportEnvelope<"ranking" | "ranking_best", RankingRow>
  | ReportEnvelope<"on_time", OnTimeRow>
  | ReportEnvelope<"worst_5min", Worst5MinRow>
  | ReportEnvelope<"compare_ranking", CompareRankingRow>
  | ReportEnvelope<"dow_weekday" | "dow_weekend", DowRankingRow>
  | ReportEnvelope<"trend", TrendPayload>
  | ReportEnvelope<"dwell_run", DwellRunPayload>
  | ReportEnvelope<"council_summary", CouncilSummaryRow>
  | ReportEnvelope<"delay_certificate", DelayCertificateRow>;

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

/** The one documented representative observation station an agency's rain-
 *  vs-dry delay comparison is keyed to -- see
 *  pipeline/reports/weather.py for why this is one station rather than an
 *  area average. `note` is the operator's own record of why this station
 *  represents the service area. */
export type WeatherStation = {
  station_id: string;
  station_name: string;
  note: string | null;
};

/** One side (rainy or non-rainy) of the comparison. `avg_delay_sec` is
 *  pooled over every delay measurement on that side's days and is `null`
 *  exactly when the side has no days/samples; `avg_precip_mm` counts each
 *  matched day once, however many routes ran on it. */
export type WeatherDelayGroup = {
  days: number;
  samples: number;
  avg_delay_sec: number | null;
  avg_precip_mm: number | null;
};

/** One precipitation bucket -- a finer, additive breakdown of the same
 *  matched service days as `wet`/`dry`, independent of the wet-day
 *  threshold. `avg_delay_sec` is `null` exactly when the bucket has no
 *  matched days/samples, same convention as `WeatherDelayGroup`. */
export type WeatherDelayBucket = {
  label: string;
  days: number;
  samples: number;
  avg_delay_sec: number | null;
};

/** Observed rainfall matched to service days (item 129) -- see
 *  pipeline/reports/weather.py's compute_rain_delay. This is a historical
 *  observation, NOT a weather forecast and NOT a causal claim; `disclaimer`
 *  must be surfaced verbatim wherever these figures are rendered, and
 *  `attribution` must travel with them per the observation source's terms.
 *
 *  `available` is false when the agency has no representative station
 *  configured, or no in-range service day could be matched to an
 *  observation -- render nothing in that case. `delta_sec` (rainy minus
 *  non-rainy, seconds) is `null` when either side has no days, which is a
 *  real answer rather than missing data; `low_confidence` is set whenever
 *  either side is thin enough that the difference should not be read as a
 *  stable effect. `buckets` is absent from responses cached before the
 *  backend started populating it, and may also be an empty array; treat
 *  both the same as "nothing to show". */
export type WeatherDelayResponse = {
  available: boolean;
  station: WeatherStation | null;
  wet_day_threshold_mm: number;
  wet: WeatherDelayGroup;
  dry: WeatherDelayGroup;
  delta_sec: number | null;
  low_confidence: boolean;
  ctx: ResponseCtx;
  disclaimer: string;
  attribution: string;
  buckets?: WeatherDelayBucket[];
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

/** GET /:agency/reports/suggest. `suggestion` is null when no rule produced a
 *  pick — an object body rather than a bare `null`, so the endpoint has
 *  somewhere to say more about the empty case later. */
export type SuggestionEnvelope = {
  suggestion: Suggestion | null;
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
  /** The dow/time_band/service the message's dispatch actually ran under —
   *  distinct from `args` (the tool's own arguments) and from the live,
   *  editable conversation `filter_ctx`, which can change after this
   *  message was sent. `null` for user messages and for a dispatch-free
   *  assistant reply (e.g. an LLM-grounded follow-up). */
  conditions?: { dow: string; time_band: string; service: string } | null;
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

/** GET /:agency/routes. Collection endpoints answer with an object, not a bare
 *  array, so the server can add paging or a total without breaking clients. */
export type RoutesResponse = {
  rows: Route[];
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
