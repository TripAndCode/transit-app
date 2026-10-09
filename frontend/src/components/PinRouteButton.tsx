import { Pin } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MAX_PINNED_ROUTES, togglePinnedRoute, usePinnedRoutes } from "../api/pinnedRoutes";

/** Pins a route to the rail's My routes, or takes it off. A full list
 *  turns the button down and says why, rather than dropping an older pin. */
export function PinRouteButton({ agencyId, code }: { agencyId: number; code: string }) {
  const { t } = useTranslation();
  const pins = usePinnedRoutes(agencyId);
  const pinned = pins.includes(code);
  const full = !pinned && pins.length >= MAX_PINNED_ROUTES;
  return (
    <button
      type="button"
      className="btn-ghost"
      aria-pressed={pinned}
      disabled={full}
      onClick={() => togglePinnedRoute(agencyId, code)}
      style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
    >
      <Pin size={14} strokeWidth={1.75} aria-hidden="true" fill={pinned ? "currentColor" : "none"} />
      {full ? t("nav.pin_route_full", { max: MAX_PINNED_ROUTES }) : t("nav.pin_route")}
    </button>
  );
}
