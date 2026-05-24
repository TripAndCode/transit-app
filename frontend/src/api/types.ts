export type Agency = {
  agency_id: number;
  agency_name: string;
  feed_url: string;
  static_url: string | null;
};

export type RouteSummary = {
  route_code: string;
  service_type: string | null;
  avg_delay_sec: number;
  worst_delay_sec: number;
  trips_observed: number;
  samples: number;
  last_seen_at: string | null;
};

export type RouteSummaryResponse = {
  latest_captured_at: string | null;
  date: string | null;
  routes: RouteSummary[];
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
};

export type Route = {
  route_id: string;
  route_short_name: string | null;
  route_long_name: string | null;
  route_code: string | null;
  trip_headsigns: string[];
};
