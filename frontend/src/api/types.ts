export type Agency = {
  agency_id: number;
  agency_name: string;
  feed_url: string;
  static_url: string | null;
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
  deviation_sec: number | null;
  bucket: RouteBucket;
  low_confidence: boolean;
  has_baseline: boolean;
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
  stop_name: string | null;
  avg_delay_sec: number;
  samples: number;
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
   * Always LineString for now (backend emits a single most-frequent shape per route).
   * If the backend grows MultiLineString support, widen this to GeoJSON.LineString | GeoJSON.MultiLineString
   * and flatten coords in MapTab before passing to MapLibre.
   */
  geometry: GeoJSON.LineString | null;
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
  samples: number;
  /** Comma-joined list of route_codes contributing to this stop's avg.
   *  Optional because clients with cached responses from before the
   *  field was added will still parse correctly. */
  route_codes?: string;
};

export type HeatmapCollection = GeoJSON.FeatureCollection<GeoJSON.Point, HeatmapProps> & {
  ctx?: ResponseCtx;
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

export type ReportResponse = ReportMeta & {
  text: string;
  rows: unknown[];
  ctx?: ResponseCtx;
};

export type TrendDay = {
  date: string;
  avg_min: number;
  samples: number;
  top_offenders: { route_code: string; service_type: string; avg_min: number; samples: number }[];
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
  delta_pct: number;
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
};

export type NetworkSummary = {
  from: string;
  to: string;
  agencies: NetworkAgencyRow[];
};

