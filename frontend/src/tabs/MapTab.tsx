import { useEffect, useEffectEvent, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Clock3, Maximize2, Radio, RefreshCw, Route as RouteIcon, Wifi } from "lucide-react";
import maplibregl, { Map as MLMap, Popup } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./map/mapOverrides.css";
import "./map/operationsMap.css";
import { useLiveTripProgress, useLiveTrips, useRouteShape, useTodayRouteSummary } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import type { LiveTrip } from "../api/types";
import { useRouteNames } from "../api/useRouteNames";
import { relativeTime } from "../utils/relativeTime";
import { buildStyle, getMapStyleOverride, readMapStylePref } from "../styles/mapStyle";
import { useMapStylePref } from "./map/useMapStylePref";
import { MapStyleControl } from "./map/MapStyleControl";
import { ErrorBanner } from "../components/ErrorBanner";
import { EmptyState } from "../components/EmptyState";
import { signedMin } from "./live/signedMin";
import { OperationsTripPanel, type ActiveRouteOption, type DirectionOption } from "./map/OperationsTripPanel";
import { useBasemapDim } from "./map/useBasemapDim";
import {
  LIVE_TRIPS_CLUSTER_LAYER,
  LIVE_TRIPS_LABEL_LAYER,
  LIVE_TRIPS_LAYER,
  useOperationsMapLayers,
} from "./map/useOperationsMapLayers";
import { buildCurrentRouteSummaries } from "./map/currentRouteStatus";

type Freshness = "normal" | "delayed" | "stale" | "unknown";
type RouteSelection = { agencyId: number | null; route: string | "all" | null };

function directionKey(trip: LiveTrip): string {
  if (trip.direction_id != null) return `direction:${trip.direction_id}`;
  if (trip.headsign) return `headsign:${trip.headsign}`;
  return "unknown";
}

function directionOptions(trips: LiveTrip[], t: ReturnType<typeof useTranslation>["t"]): DirectionOption[] {
  const grouped = new Map<string, LiveTrip[]>();
  for (const trip of trips) {
    const key = directionKey(trip);
    grouped.set(key, [...(grouped.get(key) ?? []), trip]);
  }
  return [...grouped].map(([key, rows]) => ({
    key,
    label: rows.find((trip) => trip.headsign)?.headsign ?? (
      rows[0]?.direction_id != null
        ? t("operations.trip_panel.direction_number", { direction: rows[0].direction_id })
        : t("operations.trip_panel.unknown_direction")
    ),
    trips: rows.sort((a, b) => (a.scheduled_time ?? "").localeCompare(b.scheduled_time ?? "")),
  }));
}

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
  const [selectedDirectionKey, setSelectedDirectionKey] = useState<string | null>(null);
  const [selectedTripId, setSelectedTripId] = useState<string | null>(null);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const popupRef = useRef<Popup | null>(null);
  const refreshMessageTimerRef = useRef<number | null>(null);
  const fittedRouteRef = useRef<string | null>(null);
  const firstStyleRunRef = useRef(true);
  const initialLanguageRef = useRef(i18n.language);

  const liveQuery = useLiveTrips(id);
  const summaryQuery = useTodayRouteSummary(id);
  const routeNames = useRouteNames(id);
  const liveRows = liveQuery.data?.rows ?? [];
  const activeRouteCodes = new Set(liveRows.flatMap((trip) => trip.route_code ? [trip.route_code] : []));
  const activeSummaries = buildCurrentRouteSummaries(liveRows, summaryQuery.data?.routes ?? []);
  const requestedRoute = routeSelection.agencyId === id ? routeSelection.route : null;
  // Selection only emphasizes matching trip markers and loads that route's
  // shape; the counters and priority queue continue to describe the whole feed.
  const effectiveRoute = requestedRoute && requestedRoute !== "all" && activeRouteCodes.has(requestedRoute)
    ? requestedRoute
    : null;
  const selectedSummary = activeSummaries.find((route) => route.route_code === effectiveRoute);
  const routeTrips = effectiveRoute
    ? liveRows.filter((trip) => trip.route_code === effectiveRoute)
    : [];
  const directions = directionOptions(routeTrips, t);
  const activeRouteOptions: ActiveRouteOption[] = [...activeRouteCodes].map((routeCode) => {
    const trips = liveRows.filter((trip) => trip.route_code === routeCode);
    return {
      code: routeCode,
      label: routeNames.format(routeCode),
      trips: trips.length,
      directions: directionOptions(trips, t).length,
      maxDelay: trips.reduce((maximum, trip) => Math.max(maximum, trip.dep_delay), 0),
    };
  }).sort((a, b) => b.maxDelay - a.maxDelay);
  const effectiveDirection = directions.some((direction) => direction.key === selectedDirectionKey)
    ? selectedDirectionKey
    : directions[0]?.key ?? null;
  const directionTrips = directions.find((direction) => direction.key === effectiveDirection)?.trips ?? [];
  const effectiveTrip = directionTrips.find((trip) => trip.trip_id === selectedTripId) ?? directionTrips[0] ?? null;
  const progressQuery = useLiveTripProgress(id, effectiveTrip?.trip_id ?? null);
  const shapeQuery = useRouteShape(id, effectiveRoute, ctx);
  const freshness = freshnessFor(liveQuery.data?.latest_captured_at);

  const onTripClick = useEffectEvent((event: maplibregl.MapLayerMouseEvent) => {
    const tripId = event.features?.[0]?.properties?.trip_id;
    const trip = liveQuery.data?.rows.find((row) => row.trip_id === tripId);
    if (!trip || trip.stop_lon == null || trip.stop_lat == null) return;
    if (trip.route_code) setRouteSelection({ agencyId: id, route: trip.route_code });
    setSelectedDirectionKey(directionKey(trip));
    setSelectedTripId(trip.trip_id);
    popupRef.current?.remove();
    popupRef.current = new Popup({ closeButton: true, closeOnClick: true, offset: 20 })
      .setLngLat([trip.stop_lon, trip.stop_lat])
      .setDOMContent(popupNode(trip, routeNames.format(trip.route_code), t))
      .addTo(mapRef.current!);
  });

  const onClusterClick = useEffectEvent((event: maplibregl.MapLayerMouseEvent) => {
    const coordinates = event.features?.[0]?.geometry;
    if (!mapRef.current || coordinates?.type !== "Point") return;
    mapRef.current.easeTo({
      center: coordinates.coordinates as [number, number],
      zoom: Math.min(mapRef.current.getZoom() + 2, 16),
      duration: 400,
    });
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
    map.on("click", LIVE_TRIPS_CLUSTER_LAYER, onClusterClick);
    map.on("mouseenter", LIVE_TRIPS_LAYER, onEnter);
    map.on("mouseenter", LIVE_TRIPS_LABEL_LAYER, onEnter);
    map.on("mouseenter", LIVE_TRIPS_CLUSTER_LAYER, onEnter);
    map.on("mouseleave", LIVE_TRIPS_LAYER, onLeave);
    map.on("mouseleave", LIVE_TRIPS_LABEL_LAYER, onLeave);
    map.on("mouseleave", LIVE_TRIPS_CLUSTER_LAYER, onLeave);
    mapRef.current = map;
    return () => {
      popupRef.current?.remove();
      popupRef.current = null;
      map.off("click", LIVE_TRIPS_LAYER, onTripClick);
      map.off("click", LIVE_TRIPS_LABEL_LAYER, onTripClick);
      map.off("click", LIVE_TRIPS_CLUSTER_LAYER, onClusterClick);
      map.off("mouseenter", LIVE_TRIPS_LAYER, onEnter);
      map.off("mouseenter", LIVE_TRIPS_LABEL_LAYER, onEnter);
      map.off("mouseenter", LIVE_TRIPS_CLUSTER_LAYER, onEnter);
      map.off("mouseleave", LIVE_TRIPS_LAYER, onLeave);
      map.off("mouseleave", LIVE_TRIPS_LABEL_LAYER, onLeave);
      map.off("mouseleave", LIVE_TRIPS_CLUSTER_LAYER, onLeave);
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

  useEffect(() => {
    if (!effectiveRoute) {
      fittedRouteRef.current = null;
      return;
    }
    if (!mapRef.current || !shapeQuery.data?.geometry || fittedRouteRef.current === effectiveRoute) return;
    const geometry = shapeQuery.data.geometry;
    const coordinates = geometry.type === "LineString" ? geometry.coordinates : geometry.coordinates.flat();
    if (coordinates.length === 0) return;
    const bounds = new maplibregl.LngLatBounds();
    for (const coordinate of coordinates) bounds.extend(coordinate as [number, number]);
    mapRef.current.fitBounds(bounds, { padding: 65, maxZoom: 13, duration: 500 });
    fittedRouteRef.current = effectiveRoute;
  }, [effectiveRoute, shapeQuery.data]);

  useBasemapDim(mapRef, styleEpoch, true);
  useOperationsMapLayers(
    mapRef,
    liveQuery.data,
    shapeQuery.data,
    effectiveRoute,
    selectedSummary?.avg_delay_sec ?? 0,
    id,
    styleEpoch,
    effectiveTrip?.trip_id ?? null,
    progressQuery.data,
  );

  function focusRoute(routeCode: string) {
    setRouteSelection({ agencyId: id, route: routeCode });
    setSelectedDirectionKey(null);
    setSelectedTripId(null);
    const trip = liveRows
      .filter((row) => row.route_code === routeCode && row.stop_lon != null && row.stop_lat != null)
      .sort((a, b) => b.dep_delay - a.dep_delay)[0];
    if (trip && mapRef.current) {
      mapRef.current.easeTo({ center: [trip.stop_lon!, trip.stop_lat!], zoom: Math.max(mapRef.current.getZoom(), 13), duration: 500 });
    }
  }

  function fitAllTrips() {
    const located = liveRows.filter((trip) => trip.stop_lon != null && trip.stop_lat != null);
    if (!mapRef.current || located.length === 0) return;
    const bounds = new maplibregl.LngLatBounds();
    for (const trip of located) bounds.extend([trip.stop_lon!, trip.stop_lat!]);
    mapRef.current.fitBounds(bounds, { padding: 70, maxZoom: 14, duration: 500 });
  }

  async function refreshOperations() {
    const previousObservation = liveQuery.data?.latest_captured_at ?? null;
    if (refreshMessageTimerRef.current != null) {
      window.clearTimeout(refreshMessageTimerRef.current);
      refreshMessageTimerRef.current = null;
    }
    setRefreshMessage(t("operations.refreshing"));
    const [liveResult, summaryResult, progressResult] = await Promise.all([
      liveQuery.refetch(),
      summaryQuery.refetch(),
      effectiveTrip ? progressQuery.refetch() : Promise.resolve(null),
    ]);
    if (liveResult.isError || summaryResult.isError || progressResult?.isError) {
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

  const locatedTrips = liveRows.filter((trip) => trip.stop_lat != null && trip.stop_lon != null).length;
  const delayedTrips = liveRows.filter((trip) => trip.dep_delay >= 60).length;
  const maxDelay = liveRows.reduce((maximum, trip) => Math.max(maximum, trip.dep_delay), 0);
  const selectedProgress = progressQuery.data?.stops ?? [];
  const delayGrowing = selectedProgress.some((stop, index) => index > 0 && stop.dep_delay - selectedProgress[index - 1].dep_delay >= 60);
  const delayRecovering = selectedProgress.some((stop, index) => index > 0 && selectedProgress[index - 1].dep_delay - stop.dep_delay >= 60);
  const trendLabel = !effectiveTrip || selectedProgress.length < 2
    ? t("operations.stats.waiting")
    : t(delayGrowing ? "operations.stats.growing" : delayRecovering ? "operations.stats.recovering" : "operations.stats.stable");

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
              : (() => { setRouteSelection({ agencyId: id, route: "all" }); setSelectedDirectionKey(null); setSelectedTripId(null); })()}
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
          <span className="ops-refresh__label">{t("operations.refresh_now")}</span>
        </button>
        {refreshMessage && (
          <span className="ops-refresh-result" aria-live="polite">{refreshMessage}</span>
        )}
      </header>

      <section className="ops-stats" aria-label={t("operations.summary_label")}>
        <Stat icon={<RouteIcon />} label={t("operations.stats.active")} value={liveRows.length} tone="ok" />
        <Stat icon={<Clock3 />} label={t("operations.stats.max_delay")} value={signedMin(maxDelay, t)} tone={maxDelay >= 300 ? "danger" : "warn"} />
        <Stat icon={<AlertTriangle />} label={t("operations.stats.delayed_trips")} value={delayedTrips} tone={delayedTrips > 0 ? "warn" : "ok"} />
        <Stat icon={<Wifi />} label={t("operations.stats.trend")} value={trendLabel} tone={delayGrowing ? "danger" : "ok"} />
      </section>

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
          <div className="ops-map-legend" aria-label={t("operations.map.legend_label")}>
            <span><i className="is-current" />{t("operations.map.legend_current")}</span>
            <span><i className="is-trail" />{t("operations.map.legend_trail")}</span>
            <span><i className="is-delayed" />{t("operations.map.legend_delay")}</span>
            <span><i className="is-cluster" />{t("operations.map.legend_cluster")}</span>
          </div>
          <button type="button" className="ops-map-fit" onClick={fitAllTrips}>
            <Maximize2 size={14} />{t("operations.map.fit_all")}
          </button>
          <div className="ops-map__disclosure">
            <Radio size={15} aria-hidden="true" />
            <span>{t("operations.map.disclosure", {
              located: locatedTrips,
              total: liveRows.length,
              when: liveQuery.data?.latest_captured_at ? relativeTime(liveQuery.data.latest_captured_at) : t("operations.no_update"),
            })}</span>
          </div>
        </section>

        <OperationsTripPanel
          routeName={effectiveRoute ? routeNames.format(effectiveRoute) : t("operations.all_routes")}
          activeRoutes={activeRouteOptions}
          directions={directions}
          selectedDirection={effectiveDirection}
          trips={directionTrips}
          selectedTripId={effectiveTrip?.trip_id ?? null}
          progress={progressQuery.data}
          progressLoading={progressQuery.isLoading}
          onSelectDirection={(key) => { setSelectedDirectionKey(key); setSelectedTripId(null); }}
          onSelectRoute={focusRoute}
          onSelectTrip={(trip) => {
            setSelectedTripId(trip.trip_id);
            if (trip.stop_lon != null && trip.stop_lat != null) {
              mapRef.current?.easeTo({ center: [trip.stop_lon, trip.stop_lat], zoom: Math.max(mapRef.current.getZoom(), 13), duration: 500 });
            }
          }}
          t={t}
        />
      </div>
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
