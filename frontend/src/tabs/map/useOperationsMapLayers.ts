import { useEffect, useRef } from "react";
import maplibregl, { type Map as MLMap } from "maplibre-gl";
import type { LiveTripsResponse, RouteShapeResponse } from "../../api/types";
import { delayColorResolved, severityStepColors } from "../../styles/tokens";
import { useThemeSignal } from "../../styles/theme";
import { whenStyleReady } from "./styleReady";

export const LIVE_TRIPS_SOURCE = "live-trips";
export const LIVE_TRIPS_LAYER = "live-trip-markers";
export const LIVE_TRIPS_LABEL_LAYER = "live-trip-labels";
const LIVE_TRIPS_CASING_LAYER = "live-trip-casing";
const ACTIVE_ROUTE_SOURCE = "active-route";
const ACTIVE_ROUTE_CASING_LAYER = "active-route-casing";
const ACTIVE_ROUTE_LAYER = "active-route-line";

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
          selected: trip.route_code != null && trip.route_code === selectedRoute,
        },
      }));
    const collection: GeoJSON.FeatureCollection<GeoJSON.Point> = { type: "FeatureCollection", features };

    return whenStyleReady(map, () => {
      const existing = map.getSource(LIVE_TRIPS_SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (existing) {
        existing.setData(collection);
      } else {
        map.addSource(LIVE_TRIPS_SOURCE, { type: "geojson", data: collection });
        map.addLayer({
          id: LIVE_TRIPS_CASING_LAYER,
          type: "circle",
          source: LIVE_TRIPS_SOURCE,
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
          layout: {
            "text-field": ["get", "delay_label"],
            "text-font": ["Noto Sans Regular"],
            "text-size": ["case", ["boolean", ["get", "selected"], false], 12, 10],
            "text-allow-overlap": true,
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
  }, [agencyId, live, mapRef, selectedRoute, styleEpoch, theme]);

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
}
