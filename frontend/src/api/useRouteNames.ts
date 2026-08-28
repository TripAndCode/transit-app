import { useTranslation } from "react-i18next";
import { useRoutes } from "./hooks";

/** Build a route_code → "K観光通り線 (16071)" lookup map for the agency. */ // i18n-ignore: JSDoc example
export function useRouteNames(agencyId: number | null): {
  data: Map<string, string>;
  isLoading: boolean;
  format: (route_code: string | null | undefined) => string;
} {
  const { t } = useTranslation();
  const { data, isLoading } = useRoutes(agencyId);
  const map = new Map<string, string>();
  if (data) {
    for (const r of data) {
      // Prefer route_short_name, then route_long_name, before falling back to
      // the raw route_id (which duplicates the parenthesised code inline and
      // reads worse than either GTFS name field). Mirrors the backend's own
      // COALESCE(NULLIF(short,''), NULLIF(long,''), code) order in
      // api/routers/reports.py's forecast_overview route-label query — empty
      // strings (not just null/undefined) count as "absent" on both sides.
      if (r.route_code) map.set(r.route_code, r.route_short_name || r.route_long_name || r.route_id);
    }
  }

  function format(route_code: string | null | undefined): string {
    if (!route_code) return "—";
    const name = map.get(String(route_code));
    return name ? `${name} (${route_code})` : t("common.route_code_fallback", { code: route_code });
  }

  return { data: map, isLoading, format };
}
