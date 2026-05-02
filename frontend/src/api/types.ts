export type Agency = {
  agency_id: number;
  agency_name: string;
  feed_url: string;
  static_url: string | null;
};

export type Intent = {
  query_type: string;
  unknown?: boolean;
  [key: string]: unknown;
};

export type AskResponse = {
  answer: string;
  intent: Intent;
};

export type LiveDelay = {
  trip_id: string;
  route_code: string | null;
  service_type: string | null;
  scheduled_time: string | null;
  dep_delay: number;          // seconds
  captured_at: string;        // ISO timestamp
};

export type HeatmapFeature = {
  type: "Feature";
  geometry: { type: "Point"; coordinates: [number, number] };
  properties: {
    stop_id: string;
    stop_name: string;
    avg_delay_min: number;
    samples: number;
  };
};

export type HeatmapCollection = {
  type: "FeatureCollection";
  features: HeatmapFeature[];
};

export type ReportMeta = {
  report_type: string;
  rendered_at: string;
};

export type ReportResponse = ReportMeta & {
  text: string;
  rows: Record<string, unknown>[];
};

export type Route = { route_id: string; route_short_name: string | null };
export type Stop = {
  stop_id: string;
  stop_name: string;
  stop_lat: number | null;
  stop_lon: number | null;
};
