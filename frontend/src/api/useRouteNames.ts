import { useTranslation } from "react-i18next";
import { useRoutes } from "./hooks";
import { routeDisplayName } from "./routeDisplayName";

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
      // See routeDisplayName's doc comment for the fallback order rationale.
      if (r.route_code) map.set(r.route_code, routeDisplayName(r));
    }
  }

  function format(route_code: string | null | undefined): string {
    if (!route_code) return "—";
    const name = map.get(String(route_code));
    return name ? `${name} (${route_code})` : t("common.route_code_fallback", { code: route_code });
  }

  return { data: map, isLoading, format };
}
