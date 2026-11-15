import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";
import { useIsAdmin } from "./useIsAdmin";

type StopsResponse = { rows: { stop_id: string; stop_name: string | null }[] };

/** Stop names from the static GTFS, fetched only while a stop is named. */
export function useStopNames(agencyId: number | null, needsNames: boolean) {
  const names = useQuery({
    queryKey: ["stop-names", agencyId],
    queryFn: ({ signal }) =>
      apiGet<StopsResponse>(`/api/${agencyId}/stops`, { signal }).then(
        (r) => new Map(r.rows.flatMap((s) => (s.stop_name ? [[s.stop_id, s.stop_name] as const] : []))),
      ),
    enabled: agencyId != null && needsNames,
    staleTime: 5 * 60 * 1000,
  });
  return {
    stopName: (id: string) => names.data?.get(id),
    /** Whether the names query has answered (or failed), or none was wanted. */
    settled: !needsNames || agencyId == null || !names.isPending,
  };
}

/** The stop and direction wording of a scope sentence: the stop names, and
 *  whether ids may follow a name as secondary text, which only an admin sees. */
export function usePlaceLabels(agencyId: number | null, needsNames: boolean) {
  const isAdmin = useIsAdmin();
  return { ...useStopNames(agencyId, needsNames), showIds: isAdmin };
}
