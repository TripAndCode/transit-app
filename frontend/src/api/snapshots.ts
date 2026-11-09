import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import { useLocalizedQuery } from "./hooks";
import type { ReportResponse, Route } from "./types";
import type { Locale } from "../i18n";

/** The screens a snapshot can be taken of (api/routers/snapshots.py). */
export type SnapshotScreen = "council_report" | "route_table";

/** A frozen snapshot as anyone holding its id reads it: what the screen's own
 *  endpoints served when it was taken. `report` is that endpoint's response
 *  in each language, since the server writes the council report's prose;
 *  `routes` is the agency's route catalogue the labels are drawn from;
 *  `credit` is the open-data credit, null where no source covers the feed. */
export type Snapshot = {
  snapshot_id: string;
  created_at: string;
  /** A screen this build may not render yet, such as `route_page`. */
  screen: SnapshotScreen | (string & {});
  agency_id: number;
  agency_name: string;
  params: Record<string, unknown>;
  payloads: {
    report?: Partial<Record<Locale, ReportResponse>>;
    routes?: Route[];
    credit?: Partial<Record<Locale, string | null>>;
  };
  method_version: string;
  title: string | null;
  note: string | null;
};

type SnapshotCreated = {
  snapshot_id: string;
  /** The page that renders it, on the SPA's origin. */
  path: string;
  created_at: string;
  screen: string;
  agency_id: number;
  method_version: string;
};

/** What a screen asks a snapshot of: itself, for its agency, with the query
 *  string its own report endpoint was asked (`reportQueryString`), so the
 *  server computes exactly what the screen shows. */
export type SnapshotSpec = { screen: SnapshotScreen; agencyId: number; query: string };

/** Admin only: the server computes and stores the screen's figures and
 *  answers with the snapshot's id and page. */
export function createSnapshot({ screen, agencyId, query }: SnapshotSpec): Promise<SnapshotCreated> {
  return apiPost<SnapshotCreated>("/api/admin/snapshots", {
    screen,
    agency_id: agencyId,
    query: Object.fromEntries(new URLSearchParams(query)),
  });
}

/** One snapshot by its id. It never changes, so it is read once. */
export function useSnapshot(snapshotId: string): UseQueryResult<Snapshot> {
  return useQuery({
    queryKey: ["snapshot", snapshotId],
    queryFn: ({ signal }) => apiGet<Snapshot>(`/api/snapshots/${encodeURIComponent(snapshotId)}`, { signal }),
    staleTime: Infinity,
  });
}

/** Today's figures for a snapshot's stored query
 *  (`GET /api/snapshots/{id}/today`): the screen endpoint's response as it
 *  would serve that query now, or null beside why there is none. */
export type SnapshotToday = {
  report: ReportResponse | null;
  unavailable: "agency_removed" | "not_computable" | null;
};

// How long one computation of today's figures is reused, since each ask is a
// rate-limited compute. Figures read from the aggregates move only when those
// are rebuilt; a scope the aggregates cannot serve (a time band, a tolerance
// past the delay histogram) is scanned live and can move within this window.
const TODAY_STALE_MS = 5 * 60_000;

/** Today's figures for the snapshot, asked only while `enabled`: each ask
 *  runs a report computation on a public, rate-limited path. Keyed on the UI
 *  language, as the server writes the council prose in it. */
export function useSnapshotToday(snapshotId: string, enabled: boolean): UseQueryResult<SnapshotToday> {
  return useLocalizedQuery({
    queryKey: ["snapshot-today", snapshotId],
    queryFn: ({ signal }) => apiGet<SnapshotToday>(`/api/snapshots/${encodeURIComponent(snapshotId)}/today`, { signal }),
    enabled,
    staleTime: TODAY_STALE_MS,
  });
}
