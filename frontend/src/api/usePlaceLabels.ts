import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";
import { useIsAdmin } from "./useIsAdmin";

type StopsResponse = { rows: { stop_id: string; stop_name: string | null }[] };

/** The stop and direction wording of a scope sentence: stop names from the
 *  static GTFS, fetched only while the scope names a stop, and whether ids
 *  may follow a name as secondary text, which only an admin sees. */
export function usePlaceLabels(agencyId: number | null, stop: string | null) {
  const isAdmin = useIsAdmin();
  const names = useQuery({
    queryKey: ["stop-names", agencyId],
    queryFn: ({ signal }) =>
      apiGet<StopsResponse>(`/api/${agencyId}/stops`, { signal }).then(
        (r) => new Map(r.rows.flatMap((s) => (s.stop_name ? [[s.stop_id, s.stop_name] as const] : []))),
      ),
    enabled: agencyId != null && stop != null,
    staleTime: 5 * 60 * 1000,
  });
  return { stopName: (id: string) => names.data?.get(id), showIds: isAdmin };
}
