import { useMemo } from "react";
import { useRoutes } from "./hooks";

/** Build a route_code → "K観光通り線 (16071)" lookup map for the agency. */ // i18n-ignore: JSDoc example
export function useRouteNames(agencyId: number | null): {
  data: Map<string, string>;
  isLoading: boolean;
  format: (route_code: string | null | undefined) => string;
} {
  const { data, isLoading } = useRoutes(agencyId);
  const map = useMemo(() => {
    const m = new Map<string, string>();
    if (!data) return m;
    for (const r of data) {
      if (r.route_code) m.set(r.route_code, r.route_short_name || r.route_id);
    }
    return m;
  }, [data]);

  function format(route_code: string | null | undefined): string {
    if (!route_code) return "—";
    const name = map.get(String(route_code));
    return name ? `${name} (${route_code})` : `系統${route_code}`;
  }

  return { data: map, isLoading, format };
}
