import { useEffect, useRef } from "react";
import maplibregl, { type LayerSpecification, type Map as MLMap } from "maplibre-gl";
import type { LiveTripProgressResponse, LiveTripsResponse, RouteShapeResponse } from "../../api/types";
import {
  DELAY_THRESHOLDS,
  accentColorResolved,
  delayColorResolved,
  readableInkOn,
  severityStepColors,
  surfaceColorResolved,
} from "../../styles/tokens";
import { useThemeSignal } from "../../styles/theme";
import { revealAgency } from "./cameraChoreography";
import { whenStyleReady } from "./styleReady";
import { hhmm } from "./format";

export const LIVE_TRIPS_SOURCE = "live-trips";
export const LIVE_TRIPS_LAYER = "live-trip-markers";
export const LIVE_TRIPS_LABEL_LAYER = "live-trip-labels";
export const LIVE_TRIPS_CLUSTER_LAYER = "live-trip-clusters";
const LIVE_TRIPS_CLUSTER_COUNT_LAYER = "live-trip-cluster-count";
const ACTIVE_ROUTE_SOURCE = "active-route";
const ACTIVE_ROUTE_CASING_LAYER = "active-route-casing";
const ACTIVE_ROUTE_LAYER = "active-route-line";
const TRIP_PROGRESS_SOURCE = "trip-progress";
const TRIP_PROGRESS_LINE_LAYER = "trip-progress-line";
const TRIP_PROGRESS_DIRECTION_LAYER = "trip-progress-direction";
const TRIP_PROGRESS_STOPS_LAYER = "trip-progress-stops";
const TRIP_PROGRESS_LABELS_LAYER = "trip-progress-labels";

type CirclePaint = NonNullable<Extract<LayerSpecification, { type: "circle" }>["paint"]>;
type SymbolPaint = NonNullable<Extract<LayerSpecification, { type: "symbol" }>["paint"]>;
/** MapLibre's `ExpressionSpecification` is not re-exported from the bundle's
 *  public types, so it is recovered from a property that accepts one. */
type NumericExpression = Extract<CirclePaint["circle-radius"], unknown[]>;

/** MapLibre aggregates cluster properties additively only, so a cluster's mean
 *  delay is the summed member delay divided by point_count, evaluated at paint
 *  time. In minutes, to match the ramp's thresholds. */
function clusterMeanDelayMin(): NumericExpression {
  return ["/", ["/", ["get", "delay_sum"], ["get", "point_count"]], 60];
}

/** Aggregation the live-trips source has to carry for a cluster to know
 *  anything about its members' delay. Without it a cluster can only be sized,
 *  not coloured, and 30 identical pucks say nothing about where to look. */
export const LIVE_TRIPS_CLUSTER_PROPERTIES = { delay_sum: ["+", ["get", "delay_sec"]] } as const;

/** One vehicle is one reading, not a headline: the mark is small enough that a
 *  screenful of them reads as a field rather than a wall of alarms, and only
 *  the severe tier and the selected trip earn extra area. Size therefore
 *  encodes severity, never volume. */
export const VEHICLE_RADIUS = { base: 6, severe: 9, selected: 11 } as const;

/** Clusters are sized by how many readings they stand for — that is the one
 *  place on this map where count IS the quantity being shown. */
export const CLUSTER_RADIUS = { small: 12, medium: 16, large: 22 } as const;

const MARK_STROKE_WIDTH = 1.5;

export function vehicleCirclePaint(): CirclePaint {
  return {
    "circle-radius": [
      "case",
      ["boolean", ["get", "selected"], false], VEHICLE_RADIUS.selected,
      [">=", ["/", ["get", "delay_sec"], 60], DELAY_THRESHOLDS.severe], VEHICLE_RADIUS.severe,
      VEHICLE_RADIUS.base,
    ],
    "circle-color": ["step", ["/", ["get", "delay_sec"], 60], ...severityStepColors()],
    // A hairline in the page's own surface colour, rather than a heavy dark
    // casing ring: enough to separate a mark from the basemap in either theme
    // without giving every reading the weight of an incident.
    "circle-stroke-color": surfaceColorResolved(),
    "circle-stroke-width": MARK_STROKE_WIDTH,
  };
}

export function clusterCirclePaint(): CirclePaint {
  return {
    "circle-radius": [
      "step", ["get", "point_count"],
      CLUSTER_RADIUS.small,
      10, CLUSTER_RADIUS.medium,
      50, CLUSTER_RADIUS.large,
    ],
    "circle-color": ["step", clusterMeanDelayMin(), ...severityStepColors()],
    "circle-stroke-color": surfaceColorResolved(),
    "circle-stroke-width": MARK_STROKE_WIDTH,
  };
}

export function clusterCountPaint(): SymbolPaint {
  const stops = severityStepColors();
  // The count sits on the cluster's own fill, which is a ramp colour rather
  // than a theme surface — so the ink is chosen per band, not per theme.
  return {
    "text-color": [
      "step", clusterMeanDelayMin(),
      readableInkOn(stops[0]),
      stops[1], readableInkOn(stops[2]),
      stops[3], readableInkOn(stops[4]),
      stops[5], readableInkOn(stops[6]),
    ],
  };
}

/** Marker labels sit on the basemap, not on a mark, so they take the theme's
 *  own ink and halo rather than a fixed white — the heavy dark casing that
 *  used to back them is gone. */
export function labelPaint(): SymbolPaint {
  const surface = surfaceColorResolved();
  return {
    "text-color": readableInkOn(surface),
    "text-halo-color": surface,
    "text-halo-width": 1.2,
  };
}

// Not `signedMin` (used elsewhere for the same +/-minutes shape): this
// label feeds a MapLibre GeoJSON feature property rendered by a style
// expression, which can't call `t()`, so it can't go through the
// translated formatter.
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
          trip_label: `${hhmm(trip)}  ${delayLabel(trip.dep_delay)}`,
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
        map.addSource(LIVE_TRIPS_SOURCE, {
          type: "geojson",
          data: collection,
          cluster: true,
          clusterMaxZoom: 15,
          clusterRadius: 28,
          clusterProperties: LIVE_TRIPS_CLUSTER_PROPERTIES,
        });
        map.addLayer({
          id: LIVE_TRIPS_CLUSTER_LAYER,
          type: "circle",
          source: LIVE_TRIPS_SOURCE,
          filter: ["has", "point_count"],
          paint: clusterCirclePaint(),
        });
        map.addLayer({
          id: LIVE_TRIPS_CLUSTER_COUNT_LAYER,
          type: "symbol",
          source: LIVE_TRIPS_SOURCE,
          filter: ["has", "point_count"],
          layout: { "text-field": ["get", "point_count_abbreviated"], "text-size": 11 },
          paint: clusterCountPaint(),
        });
        map.addLayer({
          id: LIVE_TRIPS_LAYER,
          type: "circle",
          source: LIVE_TRIPS_SOURCE,
          filter: ["!", ["has", "point_count"]],
          paint: vehicleCirclePaint(),
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
            // Ems, against a mark that is now a third of its old radius: a
            // larger offset leaves the label floating free of the dot it
            // names.
            "text-offset": [0, 1.1],
            "text-anchor": "top",
            "text-allow-overlap": false,
            "text-optional": true,
            "symbol-sort-key": ["case", ["boolean", ["get", "selected"], false], 0, 1],
          },
          paint: labelPaint(),
        });
      }

      if (agencyId != null && fittedAgencyRef.current !== agencyId && features.length > 0) {
        const bounds = new maplibregl.LngLatBounds();
        for (const feature of features) bounds.extend(feature.geometry.coordinates as [number, number]);
        revealAgency(map, bounds);
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
      const beforeId = map.getLayer(LIVE_TRIPS_LAYER) ? LIVE_TRIPS_LAYER : undefined;
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
      const beforeId = map.getLayer(LIVE_TRIPS_LAYER) ? LIVE_TRIPS_LAYER : undefined;
      map.addLayer({
        id: TRIP_PROGRESS_LINE_LAYER,
        type: "line",
        source: TRIP_PROGRESS_SOURCE,
        filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": accentColorResolved(), "line-width": 6, "line-opacity": 0.9 },
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
        paint: {
          "text-color": readableInkOn(accentColorResolved()),
          "text-halo-color": accentColorResolved(),
          "text-halo-width": 1,
        },
      }, beforeId);
      map.addLayer({
        id: TRIP_PROGRESS_STOPS_LAYER,
        type: "circle",
        source: TRIP_PROGRESS_SOURCE,
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": ["case", ["boolean", ["get", "latest"], false], 10, 6],
          "circle-color": ["step", ["/", ["get", "delay_sec"], 60], ...severityStepColors()],
          "circle-stroke-color": surfaceColorResolved(),
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
