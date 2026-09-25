import { useEffect, useEffectEvent, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Download, Maximize2, RefreshCw } from "lucide-react";
import { FilterDock } from "./map/FilterDock";
import { buildCsv, downloadCsv, type CsvColumn } from "../components/analysis/csv";
import { Tooltip } from "../components/Tooltip";
import { BottomSheet, type SnapPoint } from "../components/BottomSheet";
import { useMediaQuery, MOBILE_BREAKPOINT_QUERY } from "../hooks/useMediaQuery";
import "../styles/focusedAnalysis.css";
import "./map/focusedOverview.css";
import maplibregl, { Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./map/operationsMap.css";
import { useLiveTripProgress, useLiveTrips, useRouteShape, useRouteStopProfile, useTodayRouteSummary } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import { useUrlPatch, useUrlState, type UrlPatch } from "../api/useUrlState";
import type { LiveTrip } from "../api/types";
import { useRouteNames } from "../api/useRouteNames";
import { useAgencyId } from "../api/useAgencyId";
import { ApiError, apiPost } from "../api/client";
import { hhmm } from "./map/format";
import { relativeTime } from "../utils/relativeTime";
import { FILTER_SEPARATOR } from "../utils/format";
import { buildStyle, getMapStyleOverride, MAP_STYLE_IDS, readMapDimPref, readMapStylePref, writeMapDimPref } from "../styles/mapStyle";
import { useMapStylePref } from "./map/useMapStylePref";
import { MapStyleControl } from "./map/MapStyleControl";
import { ErrorBanner } from "../components/ErrorBanner";
import { EmptyState } from "../components/EmptyState";
import { MapReference } from "./map/MapReference";
import { StatTile } from "../components/StatTile";
import { signedMin } from "./live/signedMin";
import { OperationsTripPanel, type ActiveRouteOption, type DirectionOption } from "./map/OperationsTripPanel";
import { useBasemapDim } from "./map/useBasemapDim";
import { QueueResizer } from "./map/QueueResizer";
import { readQueueWidth, storeQueueWidth } from "./map/queueWidth";
import {
  LIVE_TRIPS_CLUSTER_LAYER,
  LIVE_TRIPS_LABEL_LAYER,
  LIVE_TRIPS_LAYER,
  useOperationsMapLayers,
} from "./map/useOperationsMapLayers";
import { buildCurrentRouteSummaries } from "./map/currentRouteStatus";
import { PlaybackRail } from "./map/PlaybackRail";
import { useDayPlayback } from "./map/useDayPlayback";
import { useTimelineLayers } from "./map/useTimelineLayers";
import { filterLiveRows, MAX_REPORT_AGE_MS } from "./map/liveRowsFilter";
import { nextBoundaryMs } from "./map/staleness";
import { createSafeMap } from "./map/createSafeMap";
import { useCappedList } from "../hooks/useCappedList";

const DELAYED_TRIPS_CAP = 200;

// Upper bound between staleness re-checks when no row has a boundary due
// sooner (e.g. an idle map with no live rows, or a system clock jump) --
// scheduling only ever exact boundaries would otherwise never re-check.
const STALENESS_SAFETY_TICK_MS = 60_000;
import { fitAll, focusRoute as frameRoute, inspectTrip } from "./map/cameraChoreography";
import { InspectCard } from "./map/InspectCard";

/** Clusters stop expanding here: past it MapLibre's own clusterMaxZoom has
 *  already broken them into individual vehicles, so a further step would move
 *  the camera for nothing. */
const CLUSTER_STEP_MAX_ZOOM = 16;

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

export function MapTab() {
  const id = useAgencyId();
  const { t, i18n } = useTranslation();
  const { t: td } = useTranslation("design");
  const [ctx] = useRangeContext();
  const [now, setNow] = useState(Date.now);
  // The URL is the source of truth for the *current* view (so copy-link
  // reproduces it), but localStorage stays the source of truth for a fresh
  // visit's *default* -- `useUrlState`'s default is this render's persisted
  // value, so a URL with no `style` param falls back to it, and setting a
  // new style writes both, so the next fresh visit (no URL override) picks
  // it up too.
  const [persistedStyleId, setPersistedStyleId] = useMapStylePref();
  const [styleId, setStyleIdParam] = useUrlState("style", persistedStyleId, MAP_STYLE_IDS);
  function setStyleId(next: typeof persistedStyleId) {
    setPersistedStyleId(next);
    setStyleIdParam(next);
  }
  const [dimAmount, setDimAmountState] = useState(readMapDimPref);
  const [styleEpoch, setStyleEpoch] = useState(0);
  const [mapUnavailable, setMapUnavailable] = useState(false);
  // `route_focus_agency` guards against a stale `route_focus` value matching
  // a different agency's route code after an agency switch, the same way
  // the old `RouteSelection.agencyId` field did.
  const [routeFocusAgencyParam] = useUrlState<string>("route_focus_agency", "");
  const [routeFocusParam] = useUrlState<string>("route_focus", "");
  const patchUrl = useUrlPatch();
  const routeSelection: RouteSelection = {
    agencyId: routeFocusAgencyParam ? Number(routeFocusAgencyParam) : null,
    route: routeFocusParam || null,
  };
  // Route focus, direction and trip are three keys of one selection, and
  // most actions here move more than one of them at once. They go through a
  // single patch for the reason `useUrlPatch` documents: per-key setters
  // fired from one handler overwrite each other.
  function patchSelection(next: {
    route?: string | null;
    direction?: string | null;
    trip?: string | null;
    routes?: string[];
  }) {
    const patch: UrlPatch = {};
    if ("route" in next) {
      patch.route_focus = next.route ?? null;
      patch.route_focus_agency = next.route != null && id != null ? String(id) : null;
    }
    if ("direction" in next) patch.direction = next.direction ?? null;
    if ("trip" in next) patch.trip = next.trip ?? null;
    if ("routes" in next) patch.routes = next.routes ?? null;
    patchUrl(patch);
  }
  const [queueWidth, setQueueWidth] = useState(readQueueWidth);
  const [selectedDirectionKeyParam] = useUrlState<string>("direction", "");
  const selectedDirectionKey = selectedDirectionKeyParam || null;
  const [selectedTripIdParam] = useUrlState<string>("trip", "");
  const selectedTripId = selectedTripIdParam || null;
  function setSelectedTripId(next: string | null) {
    patchSelection({ trip: next });
  }
  // Hover is pointer state, not a view worth reproducing from a link.
  const [hoveredTripId, setHoveredTripId] = useState<string | null>(null);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [playbackOn, setPlaybackOn] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const isMobile = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  const [sheetSnap, setSheetSnap] = useState<SnapPoint>("peek");
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const refreshMessageTimerRef = useRef<number | null>(null);
  const refreshAbortRef = useRef<AbortController | null>(null);
  const fittedRouteRef = useRef<string | null>(null);
  const firstStyleRunRef = useRef(true);
  const initialLanguageRef = useRef(i18n.language);

  const liveQuery = useLiveTrips(id);
  // Ticks `now` only at the next moment a row would actually change bucket
  // (leave the live-rows age window, or move the header freshness badge),
  // instead of polling every 15s and re-pushing the GeoJSON source on every
  // tick regardless of whether anything crossed a boundary. Re-armed on
  // every fetch (liveQuery.data) since new rows shift where that boundary
  // is; the recursive schedule (rather than depending on `now`) keeps
  // re-arming itself between fetches without re-running this effect.
  useEffect(() => {
    let timer: number | undefined;
    const schedule = () => {
      const boundary = nextBoundaryMs(liveQuery.data?.rows ?? [], Date.now());
      const delay = Math.min(
        boundary != null ? Math.max(boundary - Date.now(), 0) : STALENESS_SAFETY_TICK_MS,
        STALENESS_SAFETY_TICK_MS,
      );
      timer = window.setTimeout(() => {
        setNow(Date.now());
        schedule();
      }, delay);
    };
    schedule();
    return () => window.clearTimeout(timer);
  }, [liveQuery.data]);
  const summaryQuery = useTodayRouteSummary(id);
  const routeNames = useRouteNames(id);
  const liveRows = filterLiveRows(liveQuery.data?.rows ?? [], now, ctx.routes);
  const liveCsvColumns: CsvColumn<LiveTrip>[] = [
    { header: "agency_id", value: () => id },
    { header: "route_code", value: (r) => r.route_code },
    { header: "trip_id", value: (r) => r.trip_id },
    { header: "headsign", value: (r) => r.headsign },
    { header: "stop_id", value: (r) => r.stop_id },
    { header: "stop_name", value: (r) => r.stop_name },
    { header: "departure_delay_seconds", value: (r) => r.dep_delay },
    { header: "captured_at", value: (r) => r.captured_at },
  ];
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
  const stopProfileQuery = useRouteStopProfile(id, effectiveRoute);
  const freshness = freshnessFor(liveQuery.data?.latest_captured_at);

  const onTripClick = useEffectEvent((event: maplibregl.MapLayerMouseEvent) => {
    const tripId = event.features?.[0]?.properties?.trip_id;
    const trip = liveQuery.data?.rows.find((row) => row.trip_id === tripId);
    if (!trip || trip.stop_lon == null || trip.stop_lat == null) return;
    patchSelection({
      ...(trip.route_code ? { route: trip.route_code } : {}),
      direction: directionKey(trip),
      trip: trip.trip_id,
    });
    setHoveredTripId(null);
    if (mapRef.current) inspectTrip(mapRef.current, [trip.stop_lon, trip.stop_lat]);
  });

  const onTripHover = useEffectEvent((event: maplibregl.MapLayerMouseEvent) => {
    const tripId = event.features?.[0]?.properties?.trip_id;
    setHoveredTripId(typeof tripId === "string" ? tripId : null);
  });

  const onTripHoverEnd = useEffectEvent(() => setHoveredTripId(null));

  const onClusterClick = useEffectEvent((event: maplibregl.MapLayerMouseEvent) => {
    const coordinates = event.features?.[0]?.geometry;
    if (!mapRef.current || coordinates?.type !== "Point") return;
    // A cluster steps in by a fixed amount rather than to the trip-inspect
    // floor: the operator is asking "what is inside this puck", and jumping
    // straight to street level would lose the surrounding clusters they are
    // comparing it against.
    inspectTrip(
      mapRef.current,
      coordinates.coordinates as [number, number],
      Math.min(mapRef.current.getZoom() + 2, CLUSTER_STEP_MAX_ZOOM),
    );
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
    // mousemove, not mouseenter: crossing from one vehicle straight onto
    // another inside the same layer fires no new enter, so the card would
    // keep previewing the vehicle the pointer already left.
    map.on("mousemove", LIVE_TRIPS_LAYER, onTripHover);
    map.on("mousemove", LIVE_TRIPS_LABEL_LAYER, onTripHover);
    map.on("mouseleave", LIVE_TRIPS_LAYER, onTripHoverEnd);
    map.on("mouseleave", LIVE_TRIPS_LABEL_LAYER, onTripHoverEnd);
    mapRef.current = map;
    return () => {
      map.off("click", LIVE_TRIPS_LAYER, onTripClick);
      map.off("click", LIVE_TRIPS_LABEL_LAYER, onTripClick);
      map.off("click", LIVE_TRIPS_CLUSTER_LAYER, onClusterClick);
      map.off("mouseenter", LIVE_TRIPS_LAYER, onEnter);
      map.off("mouseenter", LIVE_TRIPS_LABEL_LAYER, onEnter);
      map.off("mouseenter", LIVE_TRIPS_CLUSTER_LAYER, onEnter);
      map.off("mouseleave", LIVE_TRIPS_LAYER, onLeave);
      map.off("mouseleave", LIVE_TRIPS_LABEL_LAYER, onLeave);
      map.off("mouseleave", LIVE_TRIPS_CLUSTER_LAYER, onLeave);
      map.off("mousemove", LIVE_TRIPS_LAYER, onTripHover);
      map.off("mousemove", LIVE_TRIPS_LABEL_LAYER, onTripHover);
      map.off("mouseleave", LIVE_TRIPS_LAYER, onTripHoverEnd);
      map.off("mouseleave", LIVE_TRIPS_LABEL_LAYER, onTripHoverEnd);
      map.remove();
      mapRef.current = null;
      firstStyleRunRef.current = true;
    };
  }, []);

  useEffect(() => () => {
    if (refreshMessageTimerRef.current != null) window.clearTimeout(refreshMessageTimerRef.current);
    // Cancel a manual refresh's in-flight POST on unmount so it can't land
    // (and setIsRefreshing/showRefreshMessage a now-unmounted component)
    // after the user has navigated away.
    refreshAbortRef.current?.abort();
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
    map.setStyle(buildStyle(styleId, i18n.language), { diff: false });
    map.once("style.load", () => setStyleEpoch((epoch) => epoch + 1));
  }, [i18n.language, styleId]);

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
    frameRoute(mapRef.current, bounds);
    fittedRouteRef.current = effectiveRoute;
  }, [effectiveRoute, shapeQuery.data]);

  const playback = useDayPlayback(id, playbackOn);

  useBasemapDim(mapRef, styleEpoch, true, dimAmount);
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
    stopProfileQuery.data?.stops,
  );
  // Declared after useOperationsMapLayers: effects run in declaration order,
  // so on a style reload (which wipes every imperatively-added layer) the live
  // layers are re-added before playback hides them again.
  useTimelineLayers(mapRef, styleEpoch, playback.frames, playback.index, playbackOn, playback.steppingOnly, playback.pause);

  /** Focus a row's route and select that row's own run in one write. */
  function focusTripRow(trip: LiveTrip) {
    patchSelection({
      ...(trip.route_code ? { route: trip.route_code } : {}),
      direction: directionKey(trip),
      trip: trip.trip_id,
    });
    if (trip.stop_lon != null && trip.stop_lat != null && mapRef.current) {
      inspectTrip(mapRef.current, [trip.stop_lon, trip.stop_lat]);
    }
  }

  function focusRoute(routeCode: string) {
    patchSelection({ route: routeCode, direction: null, trip: null });
    const trip = liveRows
      .filter((row) => row.route_code === routeCode && row.stop_lon != null && row.stop_lat != null)
      .sort((a, b) => b.dep_delay - a.dep_delay)[0];
    if (trip && mapRef.current) {
      inspectTrip(mapRef.current, [trip.stop_lon!, trip.stop_lat!]);
    }
  }

  function fitAllTrips() {
    const located = liveRows.filter((trip) => trip.stop_lon != null && trip.stop_lat != null);
    if (!mapRef.current || located.length === 0) return;
    const bounds = new maplibregl.LngLatBounds();
    for (const trip of located) bounds.extend([trip.stop_lon!, trip.stop_lat!]);
    fitAll(mapRef.current, bounds);
  }

  async function refreshOperations() {
    if (isRefreshing || id == null) return;
    if (refreshMessageTimerRef.current != null) {
      window.clearTimeout(refreshMessageTimerRef.current);
      refreshMessageTimerRef.current = null;
    }
    setRefreshMessage(t("operations.refreshing"));
    setIsRefreshing(true);
    const controller = new AbortController();
    refreshAbortRef.current = controller;
    try {
      const refreshResult = await apiPost<{ status: string; inserted: number }>(
        `/api/${id}/delays/refresh`,
        {},
        { signal: controller.signal },
      );
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
  const delayedRowsResetKey = `${id ?? "none"}:${liveQuery.dataUpdatedAt}:${ctx.routes.join(",")}`;
  const cappedDelayedRows = useCappedList(delayedRows, DELAYED_TRIPS_CAP, delayedRowsResetKey);

  // A hovered vehicle always wins the card: the pointer is the more recent
  // intent. Dropping the hover restores whatever was pinned, so a preview
  // never costs the operator the trip they were watching.
  const hoveredTrip = hoveredTripId ? liveRows.find((trip) => trip.trip_id === hoveredTripId) ?? null : null;
  const pinnedTrip = selectedTripId ? effectiveTrip : null;
  const inspected = hoveredTrip ?? pinnedTrip;
  const inspectedVehicles = inspected?.route_code
    ? liveRows.filter((trip) => trip.route_code === inspected.route_code).length
    : 0;

  // Shared between the desktop side column (<aside>) and the phone bottom
  // sheet -- same queue/inspect/timeline content, just a different container
  // around it, so the two layouts can never drift into showing different
  // trips.
  const queueContent = (
    <>
          <h2 className="focus-queue-title">{td("attention")}</h2>
          {/* The only live reading of these counts on the screen. Repeating
              them next to the filters invited the reader to check whether the
              two agreed instead of reading either; staleness likewise reads
              once, from the header's freshness dot. */}
          <div className="focus-summary-strip">
            <StatTile label={td("observedLabel")} value={liveRows.length} />
            <StatTile label={td("delayedLabel")} value={delayedRows.length} flagged={delayedRows.length > 0} />
            {onTimePct != null && <StatTile label={td("onTimePct")} value={onTimePct} suffix="%" />}
          </div>
          {/* Directly under the tiles, because the CSV is exactly the rows
              they count -- and above the delay list, so a long list can't
              push the export below the panel's scroll. */}
          <button type="button" className="btn-ghost ops-queue__export" disabled={!liveRows.length || !!liveQuery.error} onClick={() => downloadCsv(`live-${id}`, buildCsv(liveRows, liveCsvColumns))}><Download size={13} aria-hidden="true" />{td("csv")}</button>
          {!liveQuery.isLoading && !liveQuery.error && !delayedRows.length && <p className="focus-muted">{td("noDelayed")}</p>}
          {cappedDelayedRows.visible.map((trip) => <div className="focus-trip" key={trip.trip_id}>
            <button type="button" onClick={() => { focusTripRow(trip); }}>
              <span>{routeNames.format(trip.route_code)}<small>{hhmm(trip)}{FILTER_SEPARATOR}{trip.headsign}{FILTER_SEPARATOR}{trip.stop_name}</small></span>
              <b>{signedMin(trip.dep_delay, t)}</b>
            </button>
            {trip.route_code && <Link to={`/agencies/${id}/route-analysis?${new URLSearchParams({ routes: trip.route_code })}`}>{td("openAnalysis")}</Link>}
          </div>)}
          {cappedDelayedRows.remaining > 0 && (
            <button type="button" className="btn-ghost" onClick={cappedDelayedRows.showMore}>
              {t("common.show_more", { count: cappedDelayedRows.remaining })}
            </button>
          )}
          {/* FirstRunTour.tsx's second coach mark anchors here -- the panel
              where an observed trip is actually inspected. */}
          <details className="focus-queue-inspect" data-tour="map-inspect"><summary>{td("allObserved")}</summary><OperationsTripPanel
          routeName={effectiveRoute ? routeNames.format(effectiveRoute) : t("operations.all_routes")}
          activeRoutes={activeRouteOptions}
          directions={directions}
          selectedDirection={effectiveDirection}
          trips={directionTrips}
          selectedTripId={effectiveTrip?.trip_id ?? null}
          progress={progressQuery.data}
          progressLoading={progressQuery.isLoading}
          onSelectDirection={(key) => patchSelection({ direction: key, trip: null })}
          onSelectRoute={focusRoute}
          onSelectTrip={(trip) => {
            setSelectedTripId(trip.trip_id);
            if (trip.stop_lon != null && trip.stop_lat != null && mapRef.current) {
              inspectTrip(mapRef.current, [trip.stop_lon, trip.stop_lat]);
            }
          }}
          t={t}
        /></details>
        
    </>
  );

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

      {(liveQuery.error || summaryQuery.error) && (
        <ErrorBanner
          error={liveQuery.error ?? summaryQuery.error}
          onRetry={() => { void Promise.all([liveQuery.refetch(), summaryQuery.refetch()]); }}
        />
      )}

      <div className="ops-workspace" style={{ "--ops-queue-width": `${queueWidth}px` } as CSSProperties}>
        <section className="ops-map" aria-label={t("operations.map.aria_label")}>
          <div ref={mapContainerRef} className="ops-map__canvas" />
          {mapUnavailable && <div className="ops-map__empty"><p role="status">{td("mapUnavailable")}</p></div>}
          {!getMapStyleOverride() && (
            <MapStyleControl
              value={styleId}
              onChange={setStyleId}
              dimAmount={dimAmount}
              onDimChange={(amount) => { writeMapDimPref(amount); setDimAmountState(amount); }}
              mapRef={mapRef}
              lang={i18n.language}
              t={t}
            />
          )}
          {(liveQuery.isLoading || summaryQuery.isLoading) && <div className="ops-map__loading">{t("operations.loading")}</div>}
          {!mapUnavailable && !liveQuery.isLoading && liveRows.length === 0 && (
            <div className="ops-map__empty">
              <EmptyState title={t("operations.empty.title")} hint={t("operations.empty.hint")} />
            </div>
          )}
          {inspected && (
            <InspectCard
              trip={inspected}
              routeName={routeNames.format(inspected.route_code)}
              vehicles={inspectedVehicles}
              progress={inspected.trip_id === effectiveTrip?.trip_id ? progressQuery.data : undefined}
              pinned={hoveredTrip == null && pinnedTrip != null}
              onPin={() => {
                patchSelection({
                  ...(inspected.route_code ? { route: inspected.route_code } : {}),
                  direction: directionKey(inspected),
                  trip: inspected.trip_id,
                });
                setHoveredTripId(null);
              }}
              onUnpin={() => setSelectedTripId(null)}
              t={t}
            />
          )}
          <MapReference located={locatedTrips} total={liveRows.length} t={t} />
          {/* Rendered after the overlays that cover this corner
              (.ops-map__empty, .ops-map__loading) so a control is never
              buried behind decoration; the CSS pins that with a z-index too.
              The `data-tour` wrapper is `display: contents` (generates no
              box) so it can't affect FilterDock's own floating layout --
              FirstRunTour.tsx's first coach mark anchors to it. */}
          <div data-tour="filter-bar" style={{ display: "contents" }}>
            <FilterDock
              agencyId={id}
              applied={ctx.routes}
              onApply={(routes) => {
                patchSelection({ routes, route: null, trip: null, direction: null });
              }}
              playback={{ active: playbackOn, onToggle: () => setPlaybackOn(!playbackOn) }}
            />
          </div>
          {playbackOn && (
            <PlaybackRail
              controller={playback}
              onExit={() => { playback.pause(); setPlaybackOn(false); }}
              t={t}
            />
          )}
          {/* Disabled rather than silently doing nothing when there is
              nothing to frame: fitBounds only moves the camera, so with no
              located trip a press is indistinguishable from a broken button.
              The tooltip carries the part the label can't -- that this moves
              the map and changes nothing about which trips are shown. */}
          <Tooltip label={t("operations.map.fit_all_hint")}>
            <button
              type="button"
              className="ops-map-fit"
              onClick={fitAllTrips}
              disabled={locatedTrips === 0}
            >
              <Maximize2 size={14} />{t("operations.map.fit_all")}
            </button>
          </Tooltip>
        </section>

        {!isMobile && (
          <QueueResizer width={queueWidth} label={td("resizeQueue")} onWidth={setQueueWidth} onCommit={storeQueueWidth} />
        )}

        {isMobile ? (
          <BottomSheet snap={sheetSnap} onSnapChange={setSheetSnap} ariaLabel={td("attention")}>
            {queueContent}
          </BottomSheet>
        ) : (
          <aside className="focus-live-queue">{queueContent}</aside>
        )}
      </div>
    </div>
  );
}
