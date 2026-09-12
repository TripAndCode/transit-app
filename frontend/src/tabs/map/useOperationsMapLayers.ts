import { useEffect, useRef } from "react";
import maplibregl, { type Map as MLMap } from "maplibre-gl";
import type { LiveTripProgressResponse, LiveTripsResponse, RouteShapeResponse } from "../../api/types";
import { delayColorResolved, severityStepColors } from "../../styles/tokens";
import { useThemeSignal } from "../../styles/theme";
import { whenStyleReady } from "./styleReady";

export const LIVE_TRIPS_SOURCE = "live-trips";
export const LIVE_TRIPS_LAYER = "live-trip-markers";
export const LIVE_TRIPS_LABEL_LAYER = "live-trip-labels";
export const LIVE_TRIPS_CLUSTER_LAYER = "live-trip-clusters";
const LIVE_TRIPS_CASING_LAYER = "live-trip-casing";
const LIVE_TRIPS_CLUSTER_COUNT_LAYER = "live-trip-cluster-count";
const ACTIVE_ROUTE_SOURCE = "active-route";
const ACTIVE_ROUTE_CASING_LAYER = "active-route-casing";
const ACTIVE_ROUTE_LAYER = "active-route-line";
const TRIP_PROGRESS_SOURCE = "trip-progress";
const TRIP_PROGRESS_LINE_LAYER = "trip-progress-line";
const TRIP_PROGRESS_DIRECTION_LAYER = "trip-progress-direction";
const TRIP_PROGRESS_STOPS_LAYER = "trip-progress-stops";
const TRIP_PROGRESS_LABELS_LAYER = "trip-progress-labels";

function delayLabel(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 1) return "0";
  return `${minutes > 0 ? "+" : "-"}${Math.abs(minutes)}`;
}

export function useOperationsMapLayers(
  mapRef: React.MutableRefObject<MLMap | null>,
  live: LiveTripsResponse | undefined,
  shape: RouteShapeResponse | undefined,
  selectedRoute: string | null,
  selectedDelaySec: number,
  agencyId: number | null,
  styleEpoch: number,
  selectedTripId: string | null = null,
  progress?: LiveTripProgressResponse,
): void {
  const fittedAgencyRef = useRef<number | null>(null);
  const theme = useThemeSignal();

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !live) return;

    const features: GeoJSON.Feature<GeoJSON.Point>[] = live.rows
      .filter((trip) => trip.stop_lon != null && trip.stop_lat != null)
      .map((trip) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [trip.stop_lon!, trip.stop_lat!] },
        properties: {
          trip_id: trip.trip_id,
          route_code: trip.route_code ?? "",
          delay_sec: trip.dep_delay,
          delay_label: delayLabel(trip.dep_delay),
          trip_label: `${trip.scheduled_time?.slice(0, 5) ?? "--:--"}  ${delayLabel(trip.dep_delay)}`,
          route_selected: trip.route_code != null && trip.route_code === selectedRoute,
          selected: selectedTripId ? trip.trip_id === selectedTripId : trip.route_code != null && trip.route_code === selectedRoute,
        },
      }));
    const collection: GeoJSON.FeatureCollection<GeoJSON.Point> = { type: "FeatureCollection", features };

    return whenStyleReady(map, () => {
      const existing = map.getSource(LIVE_TRIPS_SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (existing) {
        existing.setData(collection);
      } else {
        map.addSource(LIVE_TRIPS_SOURCE, { type: "geojson", data: collection, cluster: true, clusterMaxZoom: 15, clusterRadius: 28 });
        map.addLayer({
          id: LIVE_TRIPS_CLUSTER_LAYER,
          type: "circle",
          source: LIVE_TRIPS_SOURCE,
          filter: ["has", "point_count"],
          paint: {
            "circle-radius": 24,
            "circle-color": "#2bc5aa",
            "circle-stroke-color": "#f4fffd",
            "circle-stroke-width": 3,
          },
        });
        map.addLayer({
          id: LIVE_TRIPS_CLUSTER_COUNT_LAYER,
          type: "symbol",
          source: LIVE_TRIPS_SOURCE,
          filter: ["has", "point_count"],
          layout: { "text-field": ["get", "point_count_abbreviated"], "text-size": 11 },
          paint: { "text-color": "#071916" },
        });
        map.addLayer({
          id: LIVE_TRIPS_CASING_LAYER,
          type: "circle",
          source: LIVE_TRIPS_SOURCE,
          filter: ["!", ["has", "point_count"]],
          paint: {
            "circle-radius": ["case", ["boolean", ["get", "selected"], false], 18, 14],
            "circle-color": "rgba(0,0,0,0)",
            "circle-stroke-color": "rgba(15,17,25,0.68)",
            "circle-stroke-width": 6,
          },
        });
        map.addLayer({
          id: LIVE_TRIPS_LAYER,
          type: "circle",
          source: LIVE_TRIPS_SOURCE,
          filter: ["!", ["has", "point_count"]],
          paint: {
            "circle-radius": ["case", ["boolean", ["get", "selected"], false], 18, 14],
            "circle-color": ["step", ["/", ["get", "delay_sec"], 60], ...severityStepColors()],
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": ["case", ["boolean", ["get", "selected"], false], 3, 2],
          },
        });
        map.addLayer({
          id: LIVE_TRIPS_LABEL_LAYER,
          type: "symbol",
          source: LIVE_TRIPS_SOURCE,
          filter: ["!", ["has", "point_count"]],
          layout: {
            "text-field": ["get", "trip_label"],
            "text-font": ["Noto Sans Regular"],
            "text-size": ["case", ["boolean", ["get", "selected"], false], 12, 10],
            "text-offset": [0, 2.1],
            "text-anchor": "top",
            "text-allow-overlap": false,
            "text-optional": true,
            "symbol-sort-key": ["case", ["boolean", ["get", "selected"], false], 0, 1],
          },
          paint: {
            "text-color": "#ffffff",
            "text-halo-color": "rgba(0,0,0,0.2)",
            "text-halo-width": 0.5,
          },
        });
      }

      if (agencyId != null && fittedAgencyRef.current !== agencyId && features.length > 0) {
        const bounds = new maplibregl.LngLatBounds();
        for (const feature of features) bounds.extend(feature.geometry.coordinates as [number, number]);
        map.fitBounds(bounds, { padding: 80, maxZoom: 14, duration: 0 });
        fittedAgencyRef.current = agencyId;
      }
    });
  }, [agencyId, live, mapRef, selectedRoute, selectedTripId, styleEpoch, theme]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const geometry = shape?.geometry;

    return whenStyleReady(map, () => {
      if (!geometry || !selectedRoute) {
        if (map.getLayer(ACTIVE_ROUTE_LAYER)) map.removeLayer(ACTIVE_ROUTE_LAYER);
        if (map.getLayer(ACTIVE_ROUTE_CASING_LAYER)) map.removeLayer(ACTIVE_ROUTE_CASING_LAYER);
        if (map.getSource(ACTIVE_ROUTE_SOURCE)) map.removeSource(ACTIVE_ROUTE_SOURCE);
        return;
      }

      const data: GeoJSON.Feature<GeoJSON.LineString | GeoJSON.MultiLineString> = {
        type: "Feature",
        properties: {},
        geometry,
      };
      const existing = map.getSource(ACTIVE_ROUTE_SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (existing) {
        existing.setData(data);
        map.setPaintProperty(ACTIVE_ROUTE_LAYER, "line-color", delayColorResolved(selectedDelaySec / 60));
        return;
      }
      map.addSource(ACTIVE_ROUTE_SOURCE, { type: "geojson", data });
      const beforeId = map.getLayer(LIVE_TRIPS_CASING_LAYER) ? LIVE_TRIPS_CASING_LAYER : undefined;
      map.addLayer({
        id: ACTIVE_ROUTE_CASING_LAYER,
        type: "line",
        source: ACTIVE_ROUTE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "rgba(255,255,255,0.94)", "line-width": 10 },
      }, beforeId);
      map.addLayer({
        id: ACTIVE_ROUTE_LAYER,
        type: "line",
        source: ACTIVE_ROUTE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": delayColorResolved(selectedDelaySec / 60), "line-width": 5 },
      }, beforeId);
    });
  }, [mapRef, selectedDelaySec, selectedRoute, shape, styleEpoch, theme]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const located = progress?.stops.filter((stop) => stop.stop_lon != null && stop.stop_lat != null) ?? [];
    const features: GeoJSON.Feature<GeoJSON.Point>[] = located.map((stop, index) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [stop.stop_lon!, stop.stop_lat!] },
      properties: {
        stop_sequence: stop.stop_sequence,
        stop_name: stop.stop_name ?? `#${stop.stop_sequence}`,
        delay_sec: stop.dep_delay,
        delay_label: delayLabel(stop.dep_delay),
        latest: index === located.length - 1,
      },
    }));
    const line: GeoJSON.Feature<GeoJSON.LineString> | null = features.length >= 2 ? {
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: features.map((feature) => feature.geometry.coordinates) },
    } : null;
    const collection: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [...(line ? [line] : []), ...features] };

    return whenStyleReady(map, () => {
      const existing = map.getSource(TRIP_PROGRESS_SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (existing) {
        existing.setData(collection);
        return;
      }
      map.addSource(TRIP_PROGRESS_SOURCE, { type: "geojson", data: collection });
      const beforeId = map.getLayer(LIVE_TRIPS_CASING_LAYER) ? LIVE_TRIPS_CASING_LAYER : undefined;
      map.addLayer({
        id: TRIP_PROGRESS_LINE_LAYER,
        type: "line",
        source: TRIP_PROGRESS_SOURCE,
        filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#2bc5aa", "line-width": 6, "line-opacity": 0.9 },
      }, beforeId);
      map.addLayer({
        id: TRIP_PROGRESS_DIRECTION_LAYER,
        type: "symbol",
        source: TRIP_PROGRESS_SOURCE,
        filter: ["==", ["geometry-type"], "LineString"],
        layout: {
          "symbol-placement": "line",
          "symbol-spacing": 80,
          "text-field": "›",
          "text-font": ["Noto Sans Regular"],
          "text-size": 18,
          "text-keep-upright": false,
          "text-rotation-alignment": "map",
        },
        paint: { "text-color": "#071916", "text-halo-color": "#dffbf5", "text-halo-width": 1 },
      }, beforeId);
      map.addLayer({
        id: TRIP_PROGRESS_STOPS_LAYER,
        type: "circle",
        source: TRIP_PROGRESS_SOURCE,
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": ["case", ["boolean", ["get", "latest"], false], 10, 6],
          "circle-color": ["step", ["/", ["get", "delay_sec"], 60], ...severityStepColors()],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": ["case", ["boolean", ["get", "latest"], false], 3, 2],
        },
      }, beforeId);
      map.addLayer({
        id: TRIP_PROGRESS_LABELS_LAYER,
        type: "symbol",
        source: TRIP_PROGRESS_SOURCE,
        filter: ["==", ["geometry-type"], "Point"],
        layout: {
          "text-field": ["concat", ["get", "stop_name"], "  ", ["get", "delay_label"]],
          "text-font": ["Noto Sans Regular"],
          "text-size": 11,
          "text-offset": [0, 1.5],
          "text-anchor": "top",
          "text-optional": true,
        },
        paint: { "text-color": "#f8fbff", "text-halo-color": "rgba(12,18,31,.9)", "text-halo-width": 2 },
      });
    });
  }, [mapRef, progress, styleEpoch, theme]);
}
