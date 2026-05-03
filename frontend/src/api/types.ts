export type Agency = {
  agency_id: number;
  agency_name: string;
  feed_url: string;
  static_url: string | null;
};

export type LiveDelay = {
  trip_id: string;
  route_code: string | null;
  service_type: string | null;
  scheduled_time: string | null;
  dep_delay: number; // seconds
  captured_at: string; // ISO timestamp
};

export type LiveResponse = {
  latest_captured_at: string | null;
  rows: LiveDelay[];
};

export type HeatmapProps = {
  stop_id: string;
  stop_name: string;
  avg_delay_min: number;
  samples: number;
};

export type HeatmapFeature = GeoJSON.Feature<GeoJSON.Point, HeatmapProps>;
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

export type TrendResponse = {
  days: TrendDay[];
  ctx: ResponseCtx;
};

export type ToolResult = {
  kind: "table" | "series" | "kv" | "empty" | "text";
  summary_jp: string;
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
  route_code: string | null;
};
export type Stop = {
  stop_id: string;
  stop_name: string;
  stop_lat: number | null;
  stop_lon: number | null;
};
