import { useEffect, useEffectEvent, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Clock3, Radio, RefreshCw, Route as RouteIcon, Wifi } from "lucide-react";
import maplibregl, { Map as MLMap, Popup } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./map/mapOverrides.css";
import "./map/operationsMap.css";
import { useLiveTrips, useRouteShape, useTodayRouteSummary } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import type { LiveTrip, RouteSummary } from "../api/types";
import { useRouteNames } from "../api/useRouteNames";
import { relativeTime } from "../utils/relativeTime";
import { buildStyle, getMapStyleOverride, readMapStylePref } from "../styles/mapStyle";
import { useMapStylePref } from "./map/useMapStylePref";
import { MapStyleControl } from "./map/MapStyleControl";
import { ErrorBanner } from "../components/ErrorBanner";
import { EmptyState } from "../components/EmptyState";
import { RouteDrilldown } from "./live/RouteDrilldown";
import { signedMin } from "./live/signedMin";
import { OperationsQueue } from "./map/OperationsQueue";
import { useBasemapDim } from "./map/useBasemapDim";
import {
  LIVE_TRIPS_LABEL_LAYER,
  LIVE_TRIPS_LAYER,
  useOperationsMapLayers,
} from "./map/useOperationsMapLayers";
import { buildCurrentRouteSummaries } from "./map/currentRouteStatus";

type Freshness = "normal" | "delayed" | "stale" | "unknown";
type RouteSelection = { agencyId: number | null; route: string | "all" | null };

function freshnessFor(timestamp: string | null | undefined): Freshness {
  if (!timestamp) return "unknown";
  const age = Date.now() - new Date(timestamp).getTime();
  if (!Number.isFinite(age) || age < 0) return "unknown";
  if (age <= 2 * 60_000) return "normal";
  if (age <= 10 * 60_000) return "delayed";
  return "stale";
}

function popupNode(trip: LiveTrip, routeName: string, t: ReturnType<typeof useTranslation>["t"]): HTMLDivElement {
  const root = document.createElement("div");
  root.className = "ops-map-popup";

  const route = document.createElement("strong");
  route.textContent = routeName;
  root.append(route);

  const delay = document.createElement("b");
  delay.textContent = t("operations.map.delay", { delay: signedMin(trip.dep_delay, t) });
  root.append(delay);

  const stop = document.createElement("span");
  stop.textContent = trip.stop_name
    ? t("operations.map.reported_stop", { stop: trip.stop_name })
    : t("operations.queue.location_unavailable");
  root.append(stop);

  const updated = document.createElement("small");
  updated.textContent = t("operations.map.updated", { when: relativeTime(trip.captured_at) });
  root.append(updated);
  return root;
}

export function MapTab() {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const { t, i18n } = useTranslation();
  const location = useLocation();
  const [ctx] = useRangeContext();
  const [styleId, setStyleId] = useMapStylePref();
  const [styleEpoch, setStyleEpoch] = useState(0);
  const [routeSelection, setRouteSelection] = useState<RouteSelection>({ agencyId: id, route: null });
  const [openRoute, setOpenRoute] = useState<RouteSummary | null>(null);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const popupRef = useRef<Popup | null>(null);
  const refreshMessageTimerRef = useRef<number | null>(null);
  const firstStyleRunRef = useRef(true);
  const initialLanguageRef = useRef(i18n.language);

  const liveQuery = useLiveTrips(id);
  const summaryQuery = useTodayRouteSummary(id);
  const routeNames = useRouteNames(id);
  const liveRows = liveQuery.data?.rows ?? [];
  const activeRouteCodes = new Set(liveRows.flatMap((trip) => trip.route_code ? [trip.route_code] : []));
  const activeSummaries = buildCurrentRouteSummaries(liveRows, summaryQuery.data?.routes ?? []);
  const requestedRoute = routeSelection.agencyId === id ? routeSelection.route : null;
  // Route selection must reflect explicit operator input; until then the
  // map, counters, and queue all describe the whole feed.
  const effectiveRoute = requestedRoute && requestedRoute !== "all" && activeRouteCodes.has(requestedRoute)
    ? requestedRoute
    : null;
  const selectedSummary = activeSummaries.find((route) => route.route_code === effectiveRoute);
  const shapeQuery = useRouteShape(id, effectiveRoute, ctx);
  const freshness = freshnessFor(liveQuery.data?.latest_captured_at);

  const onTripClick = useEffectEvent((event: maplibregl.MapLayerMouseEvent) => {
    const tripId = event.features?.[0]?.properties?.trip_id;
    const trip = liveQuery.data?.rows.find((row) => row.trip_id === tripId);
    if (!trip || trip.stop_lon == null || trip.stop_lat == null) return;
    if (trip.route_code) setRouteSelection({ agencyId: id, route: trip.route_code });
    popupRef.current?.remove();
    popupRef.current = new Popup({ closeButton: true, closeOnClick: true, offset: 20 })
      .setLngLat([trip.stop_lon, trip.stop_lat])
      .setDOMContent(popupNode(trip, routeNames.format(trip.route_code), t))
      .addTo(mapRef.current!);
  });

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: getMapStyleOverride() ?? buildStyle(readMapStylePref(), initialLanguageRef.current),
      center: [140.7474, 40.8246],
      zoom: 11,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    const onEnter = () => { map.getCanvas().style.cursor = "pointer"; };
    const onLeave = () => { map.getCanvas().style.cursor = ""; };
    map.on("click", LIVE_TRIPS_LAYER, onTripClick);
    map.on("click", LIVE_TRIPS_LABEL_LAYER, onTripClick);
    map.on("mouseenter", LIVE_TRIPS_LAYER, onEnter);
    map.on("mouseleave", LIVE_TRIPS_LAYER, onLeave);
    mapRef.current = map;
    return () => {
      popupRef.current?.remove();
      popupRef.current = null;
      map.off("click", LIVE_TRIPS_LAYER, onTripClick);
      map.off("click", LIVE_TRIPS_LABEL_LAYER, onTripClick);
      map.off("mouseenter", LIVE_TRIPS_LAYER, onEnter);
      map.off("mouseleave", LIVE_TRIPS_LAYER, onLeave);
      map.remove();
      mapRef.current = null;
      firstStyleRunRef.current = true;
    };
  }, []);

  useEffect(() => () => {
    if (refreshMessageTimerRef.current != null) window.clearTimeout(refreshMessageTimerRef.current);
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const container = mapContainerRef.current;
    if (!map || !container || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => map.resize());
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (firstStyleRunRef.current) {
      firstStyleRunRef.current = false;
      return;
    }
    if (getMapStyleOverride()) return;
    popupRef.current?.remove();
    map.setStyle(buildStyle(styleId, i18n.language), { diff: false });
    map.once("style.load", () => setStyleEpoch((epoch) => epoch + 1));
  }, [i18n.language, styleId]);

  useEffect(() => {
    // A popup anchored to one trip renders a static snapshot (delay, stop,
    // "updated X ago") captured at click time. Once the 30-second live
    // refetch lands, that trip's data may already be stale or gone from the
    // feed, so any open popup must close rather than keep showing it.
    popupRef.current?.remove();
  }, [liveQuery.data]);

  useBasemapDim(mapRef, styleEpoch, true);
  useOperationsMapLayers(
    mapRef,
    liveQuery.data,
    shapeQuery.data,
    effectiveRoute,
    selectedSummary?.avg_delay_sec ?? 0,
    id,
    styleEpoch,
  );

  function focusRoute(routeCode: string) {
    setRouteSelection({ agencyId: id, route: routeCode });
    const trip = liveRows
      .filter((row) => row.route_code === routeCode && row.stop_lon != null && row.stop_lat != null)
      .sort((a, b) => b.dep_delay - a.dep_delay)[0];
    if (trip && mapRef.current) {
      mapRef.current.easeTo({ center: [trip.stop_lon!, trip.stop_lat!], zoom: Math.max(mapRef.current.getZoom(), 13), duration: 500 });
    }
  }

  async function refreshOperations() {
    const previousObservation = liveQuery.data?.latest_captured_at ?? null;
    setRefreshMessage(t("operations.refreshing"));
    const [liveResult, summaryResult] = await Promise.all([liveQuery.refetch(), summaryQuery.refetch()]);
    if (liveResult.isError || summaryResult.isError) {
      showRefreshMessage(t("operations.refresh_failed"));
      return;
    }
    const nextObservation = liveResult.data?.latest_captured_at ?? null;
    const message = nextObservation && nextObservation !== previousObservation
      ? t("operations.refresh_updated", { when: relativeTime(nextObservation) })
      : t("operations.refresh_unchanged");
    showRefreshMessage(message);
  }

  function showRefreshMessage(message: string) {
    if (refreshMessageTimerRef.current != null) window.clearTimeout(refreshMessageTimerRef.current);
    setRefreshMessage(message);
    refreshMessageTimerRef.current = window.setTimeout(() => {
      setRefreshMessage(null);
      refreshMessageTimerRef.current = null;
    }, 8_000);
  }

  const anomalyCount = activeSummaries.filter((route) => route.bucket === "anomaly").length;
  const watchCount = activeSummaries.filter((route) => route.bucket === "watch").length;
  const locatedTrips = liveRows.filter((trip) => trip.stop_lat != null && trip.stop_lon != null).length;

  return (
    <div className="operations-page">
      <header className="ops-header">
        <div className="ops-heading">
          <Radio size={23} aria-hidden="true" />
          <div>
            <span className="ops-eyebrow">{t("operations.eyebrow")}</span>
            <h1>{t("operations.title")}</h1>
          </div>
        </div>
        <nav className="ops-mode-switch" aria-label={t("operations.mode_label")}>
          <span aria-current="page">{t("operations.mode.current")}</span>
          <Link to={`/agencies/${agencyId}/analysis/trend${location.search}`}>{t("operations.mode.history")}</Link>
        </nav>
        <label className="ops-route-filter">
          <span>{t("operations.route_filter")}</span>
          <select
            value={effectiveRoute ?? ""}
            onChange={(event) => event.target.value
              ? focusRoute(event.target.value)
              : setRouteSelection({ agencyId: id, route: "all" })}
          >
            <option value="">{t("operations.all_routes")}</option>
            {[...activeRouteCodes].sort().map((routeCode) => (
              <option key={routeCode} value={routeCode}>{routeNames.format(routeCode)}</option>
            ))}
          </select>
        </label>
        <div className={`ops-freshness ops-freshness--${freshness}`}>
          <span />
          {liveQuery.data?.latest_captured_at
            ? t("operations.last_updated", { when: relativeTime(liveQuery.data.latest_captured_at) })
            : t("operations.no_update")}
        </div>
        <button
          type="button"
          className="ops-refresh"
          aria-label={t("operations.refresh")}
          onClick={() => { void refreshOperations(); }}
          disabled={liveQuery.isFetching || summaryQuery.isFetching}
        >
          <RefreshCw size={17} aria-hidden="true" />
        </button>
        {refreshMessage && (
          <span className="ops-refresh-result" aria-live="polite">{refreshMessage}</span>
        )}
      </header>

      <section className="ops-stats" aria-label={t("operations.summary_label")}>
        <Stat icon={<RouteIcon />} label={t("operations.stats.active")} value={liveRows.length} tone="ok" />
        <Stat icon={<AlertTriangle />} label={t("operations.stats.anomaly")} value={anomalyCount} tone="danger" />
        <Stat icon={<Clock3 />} label={t("operations.stats.watch")} value={watchCount} tone="warn" />
        <Stat icon={<Wifi />} label={t("operations.stats.freshness")} value={t(`operations.freshness.${freshness}`)} tone={freshness === "normal" ? "ok" : freshness === "stale" ? "danger" : "warn"} />
      </section>

      <div className="ops-context" role="status">
        <span className="ops-context__route">
          <RouteIcon size={15} aria-hidden="true" />
          {effectiveRoute
            ? t("operations.context.route_selected", { route: routeNames.format(effectiveRoute) })
            : t("operations.context.all_routes")}
        </span>
        <span className="ops-context__hint">
          {effectiveRoute
            ? t("operations.context.selected_hint")
            : t("operations.context.all_hint")}
        </span>
      </div>

      {(liveQuery.error || summaryQuery.error) && (
        <ErrorBanner
          error={liveQuery.error ?? summaryQuery.error}
          onRetry={() => { void Promise.all([liveQuery.refetch(), summaryQuery.refetch()]); }}
        />
      )}

      <div className="ops-workspace">
        <section className="ops-map" aria-label={t("operations.map.aria_label")}>
          <div ref={mapContainerRef} className="ops-map__canvas" />
          {!getMapStyleOverride() && <MapStyleControl value={styleId} onChange={setStyleId} t={t} />}
          {(liveQuery.isLoading || summaryQuery.isLoading) && <div className="ops-map__loading">{t("operations.loading")}</div>}
          {!liveQuery.isLoading && liveRows.length === 0 && (
            <div className="ops-map__empty">
              <EmptyState title={t("operations.empty.title")} hint={t("operations.empty.hint")} />
            </div>
          )}
          <div className="ops-map__disclosure">
            <Radio size={15} aria-hidden="true" />
            <span>{t("operations.map.disclosure", {
              located: locatedTrips,
              total: liveRows.length,
              when: liveQuery.data?.latest_captured_at ? relativeTime(liveQuery.data.latest_captured_at) : t("operations.no_update"),
            })}</span>
          </div>
        </section>

        <OperationsQueue
          routes={activeSummaries}
          trips={liveRows}
          selectedRoute={effectiveRoute}
          formatRoute={routeNames.format}
          onSelectRoute={focusRoute}
          onOpenRoute={setOpenRoute}
          t={t}
        />
      </div>

      {openRoute && id != null && (
        <RouteDrilldown
          agencyId={id}
          routeCode={openRoute.route_code}
          routeName={routeNames.format(openRoute.route_code)}
          onClose={() => setOpenRoute(null)}
          t={t}
        />
      )}
    </div>
  );
}

function Stat({ icon, label, value, tone }: { icon: React.ReactElement<{ size?: number }>; label: string; value: React.ReactNode; tone: "ok" | "warn" | "danger" }) {
  return (
    <div className={`ops-stat ops-stat--${tone}`}>
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
