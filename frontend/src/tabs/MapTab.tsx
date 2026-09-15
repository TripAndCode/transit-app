import { useEffect, useEffectEvent, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Maximize2, Radio, RefreshCw } from "lucide-react";
import { PatternFilters } from "../components/analysis/AnalysisFilters";
import { downloadCsv } from "../components/analysis/csv";
import "../styles/focusedAnalysis.css";
import "./map/focusedOverview.css";
import maplibregl, { Map as MLMap, Popup } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./map/mapOverrides.css";
import "./map/operationsMap.css";
import { useLiveTripProgress, useLiveTrips, useRouteShape, useTodayRouteSummary } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import type { LiveTrip } from "../api/types";
import { useRouteNames } from "../api/useRouteNames";
import { ApiError, apiPost } from "../api/client";
import { relativeTime } from "../utils/relativeTime";
import { buildStyle, getMapStyleOverride, readMapStylePref } from "../styles/mapStyle";
import { useMapStylePref } from "./map/useMapStylePref";
import { MapStyleControl } from "./map/MapStyleControl";
import { ErrorBanner } from "../components/ErrorBanner";
import { EmptyState } from "../components/EmptyState";
import { LegendChip } from "../components/LegendChip";
import { StatTile } from "../components/StatTile";
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
import { filterLiveRows, MAX_REPORT_AGE_MS } from "./map/liveRowsFilter";
import { createSafeMap } from "./map/createSafeMap";

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
  if (age <= MAX_REPORT_AGE_MS) return "delayed";
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
  const { t: td } = useTranslation("design");
  const [ctx, updateCtx] = useRangeContext();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, []);
  const [styleId, setStyleId] = useMapStylePref();
  const [styleEpoch, setStyleEpoch] = useState(0);
  const [mapUnavailable, setMapUnavailable] = useState(false);
  const [routeSelection, setRouteSelection] = useState<RouteSelection>({ agencyId: id, route: null });
  const [selectedDirectionKey, setSelectedDirectionKey] = useState<string | null>(null);
  const [selectedTripId, setSelectedTripId] = useState<string | null>(null);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
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
  const liveRows = filterLiveRows(liveQuery.data?.rows ?? [], now, ctx.routes);
  const activeRouteCodes = new Set(liveRows.flatMap((trip) => trip.route_code ? [trip.route_code] : []));
  const activeSummaries = buildCurrentRouteSummaries(liveRows, summaryQuery.data?.routes ?? []);
  const requestedRoute = (routeSelection.agencyId === id ? routeSelection.route : null) ?? (ctx.routes.length === 1 ? ctx.routes[0] : null);
  // effectiveRoute only highlights matching markers and loads that route's shape.
  // It's independent of ctx.routes, which already scoped liveRows (and so every
  // counter/queue/CSV derived from it) above, whether ctx.routes has one entry
  // or many.
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
    const created = createSafeMap(
      {
        container: mapContainerRef.current,
        style: getMapStyleOverride() ?? buildStyle(readMapStylePref(), initialLanguageRef.current),
        center: [140.7474, 40.8246],
        zoom: 11,
      },
      () => setMapUnavailable(true),
    );
    if (!created.map) return created.cleanup;
    const map: MLMap = created.map;
    // top-right, not the default top-left: the legend now occupies top-left
    // (see .ops-map-legend) and the two used to be squeezed into the same
    // corner, forcing the legend to offset itself around the zoom buttons.
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
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
    liveQuery.data ? { ...liveQuery.data, rows: liveRows } : undefined,
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
    if (isRefreshing || id == null) return;
    if (refreshMessageTimerRef.current != null) {
      window.clearTimeout(refreshMessageTimerRef.current);
      refreshMessageTimerRef.current = null;
    }
    setRefreshMessage(t("operations.refreshing"));
    setIsRefreshing(true);
    try {
      const refreshResult = await apiPost<{ status: string; inserted: number }>(`/api/${id}/delays/refresh`, {});
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
      const message = refreshResult.inserted > 0 && nextObservation
        ? t("operations.refresh_updated", {
          when: relativeTime(nextObservation),
          count: refreshResult.inserted,
        })
        : t("operations.refresh_unchanged");
      showRefreshMessage(message);
    } catch (error) {
      showRefreshMessage(error instanceof ApiError && error.status === 429
        ? t("operations.refresh_rate_limited")
        : t("operations.refresh_failed"));
    } finally {
      setIsRefreshing(false);
    }
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
  const delayedRows = liveRows.filter((trip) => trip.dep_delay >= 300).sort((a, b) => b.dep_delay - a.dep_delay);
  const onTimePct = liveRows.length ? Math.round(((liveRows.length - delayedRows.length) / liveRows.length) * 100) : null;

  return (
    <div className="operations-page focused-overview">
      <header className="ops-header">
        <div className="ops-heading">
          <div>
            <h1>{td("live")}</h1>
          </div>
        </div>
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
          disabled={isRefreshing || liveQuery.isFetching || summaryQuery.isFetching}
          aria-busy={isRefreshing}
        >
          <RefreshCw size={17} aria-hidden="true" />
          <span className="ops-refresh__label">{isRefreshing ? t("operations.refreshing") : t("operations.refresh_now")}</span>
        </button>
        {refreshMessage && (
          <span className="ops-refresh-result" aria-live="polite">{refreshMessage}</span>
        )}
      </header>

      <div className="focus-filters">
        <PatternFilters agencyId={id} codes={ctx.routes} onChange={(routes) => {
          updateCtx({ routes }); setRouteSelection({ agencyId: id, route: null }); setSelectedTripId(null); setSelectedDirectionKey(null);
        }} />
        <span className="focus-muted">{td("observed", { count: liveRows.length })} · {td("delayed", { count: delayedRows.length })}</span>
        <button type="button" className="btn-ghost" disabled={!liveRows.length || !!liveQuery.error} onClick={() => downloadCsv(`live-${id}`, [
          ["agency_id", "route_code", "trip_id", "headsign", "stop_id", "stop_name", "departure_delay_seconds", "captured_at"],
          ...liveRows.map((r) => [id, r.route_code, r.trip_id, r.headsign, r.stop_id, r.stop_name, r.dep_delay, r.captured_at]),
        ])}>{td("csv")}</button>
      </div>
      {freshness === "stale" && <p role="status" className="focus-muted">{td("stale")}</p>}

      {(liveQuery.error || summaryQuery.error) && (
        <ErrorBanner
          error={liveQuery.error ?? summaryQuery.error}
          onRetry={() => { void Promise.all([liveQuery.refetch(), summaryQuery.refetch()]); }}
        />
      )}

      <div className="ops-workspace">
        <section className="ops-map" aria-label={t("operations.map.aria_label")}>
          <div ref={mapContainerRef} className="ops-map__canvas" />
          {mapUnavailable && <div className="ops-map__empty"><p role="status">{td("mapUnavailable")}</p></div>}
          {!getMapStyleOverride() && <MapStyleControl value={styleId} onChange={setStyleId} t={t} />}
          {(liveQuery.isLoading || summaryQuery.isLoading) && <div className="ops-map__loading">{t("operations.loading")}</div>}
          {!mapUnavailable && !liveQuery.isLoading && liveRows.length === 0 && (
            <div className="ops-map__empty">
              <EmptyState title={t("operations.empty.title")} hint={t("operations.empty.hint")} />
            </div>
          )}
          <div className="ops-map-legend" aria-label={t("operations.map.legend_label")}>
            <LegendChip color="var(--accent-strong)" label={t("operations.map.legend_current")} />
            <LegendChip color="#2bc5aa" label={t("operations.map.legend_trail")} />
            <LegendChip color="var(--delay-flag)" label={t("operations.map.legend_delay")} />
            <LegendChip color="#2bc5aa" label={t("operations.map.legend_cluster")} />
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

        <aside className="focus-live-queue">
          <h2>{td("attention")}</h2>
          <div className="focus-summary-strip">
            <StatTile label={td("observed", { count: liveRows.length })} value={String(liveRows.length)} />
            <StatTile label={td("delayed", { count: delayedRows.length })} value={String(delayedRows.length)} flagged={delayedRows.length > 0} />
            {onTimePct != null && <StatTile label={td("onTimePct")} value={`${onTimePct}%`} />}
          </div>
          {!liveQuery.isLoading && !liveQuery.error && !delayedRows.length && <p className="focus-muted">{td("noDelayed")}</p>}
          {delayedRows.map((trip) => <div className="focus-trip" key={trip.trip_id}>
            <button type="button" onClick={() => { if (trip.route_code) focusRoute(trip.route_code); setSelectedDirectionKey(directionKey(trip)); setSelectedTripId(trip.trip_id); }}>
              <span>{routeNames.format(trip.route_code)}<small>{trip.scheduled_time?.slice(0, 5)} · {trip.headsign} · {trip.stop_name}</small></span>
              <b>{signedMin(trip.dep_delay, t)}</b>
            </button>
            {trip.route_code && <Link to={`/agencies/${id}/route-analysis?${new URLSearchParams({ routes: trip.route_code })}`}>{td("openAnalysis")}</Link>}
          </div>)}
          <details><summary>{td("allObserved")}</summary><OperationsTripPanel
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
        /></details>
        </aside>
      </div>
    </div>
  );
}
