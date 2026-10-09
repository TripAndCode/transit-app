import { Fragment, useId } from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../api/hooks";
import { withQuery } from "../api/screenScope";
import { todayMeanMinutes, unusualRouteCount } from "../api/todayRouteFigures";
import { destHref, type Destination } from "../routes/destinations";
import { prefetchRouteChunk } from "../routes/lazyTabs";
import { NavIndicator } from "./NavIndicator";
import { PendingNavLink } from "./navPending";
import { usePendingNavTarget } from "./navPendingContext";
import { GO_TO_TARGETS } from "./paletteNavTargets";
import { RailTooltip } from "./RailTooltip";
import { RAIL_STOPS, railPlace } from "./railStops";
import { SIDEBAR_NAV_ITEMS } from "./sidebarNavItems";
import "./sidebarLineMap.css";

const chordFor = (to: string) => GO_TO_TARGETS.find((target) => target.to === to)?.chordKey;

function Chord({ to }: { to: string }) {
  const key = chordFor(to);
  if (!key) return null;
  return (
    <span className="rail-chord" aria-hidden="true">
      <kbd>g</kbd>
      <kbd>{key}</kbd>
    </span>
  );
}

/** The desktop rail's destinations as stations on one line, numbered in
 *  rail order. The current station opens its stops (the reports or boards
 *  its screen switches between) below itself, and Ask is a transfer off the
 *  line's end. Which station is open follows a navigation as soon as it
 *  starts, so a click opens the next station's stops before its screen
 *  arrives. */
export function SidebarLineMap({
  collapsed,
  agencyId,
  screenQuery,
}: {
  collapsed: boolean;
  agencyId: string | number;
  screenQuery: (agencyId: string | number, screen: string) => string;
}) {
  const { t } = useTranslation();
  const { pathname, search } = useLocation();
  const pendingTo = usePendingNavTarget();
  const place = (pendingTo != null ? railPlace(pendingTo) : null) ?? railPlace(`${pathname}${search}`);
  const openStops = place ? RAIL_STOPS[place.dest] : undefined;
  const endsInStops = place?.dest === SIDEBAR_NAV_ITEMS[SIDEBAR_NAV_ITEMS.length - 1].to && openStops != null;
  const askLabel = t("nav.ask");
  const askChord = chordFor("ask");
  const figureId = useId();
  // Today's figures share the top bar's query for the summary, so the two
  // never fetch it twice.
  const { data: today } = useTodayRouteSummary(Number(agencyId), { autoRefresh: false });
  const meanMinutes = todayMeanMinutes(today?.routes);
  const unusual = unusualRouteCount(today?.routes);
  const figures: Partial<Record<Destination, string>> = {
    pulse: meanMinutes == null ? undefined : t("nav.station_figure_mean", { delay: t("common.minutes_value", { value: meanMinutes.toFixed(1) }) }),
    live: unusual > 0 ? t("nav.station_figure_unusual", { n: unusual }) : undefined,
  };

  return (
    <div className="rail-line-map" data-collapsed={collapsed || undefined}>
      <nav aria-label={t("nav.destinations_label")} className="rail-line" data-ends-in-stops={endsInStops || undefined}>
        <NavIndicator
          axis="y"
          watch={pathname}
          className="rail-halo"
          current="a.rail-station.active"
          pending='a.rail-station[aria-busy="true"]'
        />
        {SIDEBAR_NAV_ITEMS.map((item, i) => {
          const label = t(item.labelKey);
          const stops = place?.dest === item.to ? openStops : undefined;
          const figure = collapsed ? undefined : figures[item.to];
          return (
            <Fragment key={item.to}>
              <RailTooltip collapsed={collapsed} label={label}>
                <PendingNavLink
                  to={withQuery(`/agencies/${agencyId}/${item.to}`, screenQuery(agencyId, item.to))}
                  className="rail-station"
                  aria-label={collapsed || figure ? label : undefined}
                  aria-describedby={figure ? `${figureId}-${item.to}` : undefined}
                  onMouseEnter={() => prefetchRouteChunk(item.to)}
                  onFocus={() => prefetchRouteChunk(item.to)}
                >
                  <span className="rail-badge" aria-hidden="true">
                    <span className="rail-badge-line">D</span>
                    <span className="rail-badge-no num">{String(i + 1).padStart(2, "0")}</span>
                  </span>
                  {!collapsed && <span className="rail-name">{label}</span>}
                  {/* The link keeps the screen's name through aria-label and
                      reads the figure as its description. */}
                  {figure && (
                    <span id={`${figureId}-${item.to}`} className="rail-figure num">
                      {figure}
                    </span>
                  )}
                  {!collapsed && <Chord to={item.to} />}
                </PendingNavLink>
              </RailTooltip>
              {stops && (
                <div role="group" aria-label={t("nav.stops_label", { screen: label })} className="rail-stops">
                  {stops.map((stop) => {
                    const name = stop.label(t);
                    const current = place?.stop === stop.id;
                    return (
                      <RailTooltip key={stop.id} collapsed={collapsed} label={name}>
                        <PendingNavLink
                          to={destHref(agencyId, item.to, screenQuery(agencyId, item.to), stop.extra)}
                          className="rail-stop"
                          // Every stop shares its station's path, so the
                          // router would call each one the current page.
                          aria-current={current ? "true" : "false"}
                          aria-label={collapsed ? name : undefined}
                          spinner={false}
                        >
                          <span className="rail-stop-dot" aria-hidden="true" />
                          {!collapsed && <span className="rail-stop-name">{name}</span>}
                        </PendingNavLink>
                      </RailTooltip>
                    );
                  })}
                </div>
              )}
            </Fragment>
          );
        })}
      </nav>
      {/* Outside the destinations: the top bar's field searches but does not
          take a question, so Ask is reached from here. */}
      <div className="rail-transfer">
        <RailTooltip collapsed={collapsed} label={askLabel}>
          <PendingNavLink
            to={withQuery(`/agencies/${agencyId}/ask`, screenQuery(agencyId, "ask"))}
            className="rail-station rail-station--transfer"
            aria-label={collapsed ? askLabel : undefined}
            onMouseEnter={() => prefetchRouteChunk("ask")}
            onFocus={() => prefetchRouteChunk("ask")}
          >
            <span className="rail-badge rail-badge--transfer" aria-hidden="true">
              {askChord?.toUpperCase()}
            </span>
            {!collapsed && (
              <span className="rail-name">
                {askLabel}
                <span className="rail-transfer-tag" aria-hidden="true">
                  {t("nav.transfer")}
                </span>
              </span>
            )}
            {!collapsed && <Chord to="ask" />}
          </PendingNavLink>
        </RailTooltip>
      </div>
    </div>
  );
}
