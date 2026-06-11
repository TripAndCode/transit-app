import { useEffect } from "react";
import { type Map as MLMap } from "maplibre-gl";
import type { RouteShapeResponse } from "../../api/types";
import { DELAY_RAMP } from "../../styles/tokens";
import { LAYER } from "./useHeatmapLayer";

const ROUTE_SOURCE = "route-line";
const ROUTE_STOPS_SOURCE = "route-line-stops";
const ROUTE_LAYER = "route-line-stroke";
export const ROUTE_STOPS_LAYER = "route-stops";
const ROUTE_UNOBS_SOURCE = "route-unobserved";
const ROUTE_UNOBS_LAYER = "route-unobserved-stops";

/**
 * Draw the single-route overlay when `shape` is present (polyline + numbered
 * stops + hollow unobserved-stop rings); strip it and re-show the heatmap
 * LAYER when it isn't. Fits bounds to the route on focus.
 */
export function useRouteOverlay(
  mapRef: React.MutableRefObject<MLMap | null>,
  styleLoadedRef: React.MutableRefObject<boolean>,
  shape: RouteShapeResponse | undefined,
  styleEpoch: number,
): void {
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;

    function clearOverlay() {
      if (!m) return;
      if (m.getLayer(ROUTE_UNOBS_LAYER)) m.removeLayer(ROUTE_UNOBS_LAYER);
      if (m.getLayer(ROUTE_STOPS_LAYER)) m.removeLayer(ROUTE_STOPS_LAYER);
      if (m.getLayer(ROUTE_LAYER)) m.removeLayer(ROUTE_LAYER);
      if (m.getSource(ROUTE_SOURCE)) m.removeSource(ROUTE_SOURCE);
      if (m.getSource(ROUTE_STOPS_SOURCE)) m.removeSource(ROUTE_STOPS_SOURCE);
      if (m.getSource(ROUTE_UNOBS_SOURCE)) m.removeSource(ROUTE_UNOBS_SOURCE);
    }

    function drawOverlay() {
      if (!m || !shape || shape.stops.length < 2) {
        clearOverlay();
        if (m && m.getLayer(LAYER)) m.setLayoutProperty(LAYER, "visibility", "visible");
        return;
      }
      clearOverlay();
      const geomCoords = shape.geometry?.coordinates;
      const coords: [number, number][] =
        geomCoords && geomCoords.length >= 2
          ? (geomCoords as [number, number][])
          : shape.stops.map((s) => [s.lon, s.lat]);

      if (m.getLayer(LAYER)) m.setLayoutProperty(LAYER, "visibility", "none");

      m.addSource(ROUTE_SOURCE, {
        type: "geojson",
        data: {
          type: "Feature",
          geometry: { type: "LineString", coordinates: coords },
          properties: {},
        },
      });
      m.addLayer({
        id: ROUTE_LAYER,
        type: "line",
        source: ROUTE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#5b6cad",
          "line-width": ["interpolate", ["linear"], ["zoom"], 8, 2, 13, 4, 17, 7],
          "line-opacity": 0.7,
        },
      });

      m.addSource(ROUTE_STOPS_SOURCE, {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: shape.stops.map((s) => ({
            type: "Feature",
            geometry: { type: "Point", coordinates: [s.lon, s.lat] },
            properties: {
              stop_sequence: s.stop_sequence,
              stop_name: s.stop_name,
              stop_id: s.stop_id ?? null,
              stop_code: s.stop_code ?? null,
              platform_code: s.platform_code ?? null,
              avg_min: s.avg_min ?? 0,
              samples: s.samples,
            },
          })),
        },
      });
      m.addLayer({
        id: ROUTE_STOPS_LAYER,
        type: "circle",
        source: ROUTE_STOPS_SOURCE,
        paint: {
          "circle-radius": [
            "interpolate", ["linear"], ["zoom"],
            10, 4,
            14, 7,
            17, 11,
          ],
          "circle-color": [
            "step",
            ["get", "avg_min"],
            DELAY_RAMP.ok,
            2, DELAY_RAMP.mild,
            5, DELAY_RAMP.moderate,
            10, DELAY_RAMP.severe,
          ],
          "circle-opacity": 0.95,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });

      const unobserved = shape.unobserved_stops ?? [];
      if (unobserved.length > 0) {
        m.addSource(ROUTE_UNOBS_SOURCE, {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: unobserved.map((s) => ({
              type: "Feature",
              geometry: { type: "Point", coordinates: [s.lon, s.lat] },
              properties: {
                stop_sequence: s.stop_sequence,
                stop_name: s.stop_name,
                stop_id: s.stop_id ?? null,
                stop_code: s.stop_code ?? null,
                platform_code: s.platform_code ?? null,
              },
            })),
          },
        });
        m.addLayer({
          id: ROUTE_UNOBS_LAYER,
          type: "circle",
          source: ROUTE_UNOBS_SOURCE,
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2.5, 14, 4, 17, 6],
            "circle-color": "rgba(255,255,255,0.0)",
            "circle-stroke-width": 1,
            "circle-stroke-color": "rgba(0,0,0,0.35)",
          },
        });
      }

      let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
      for (const [lon, lat] of coords) {
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
      }
      m.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 60, duration: 600 });
    }

    if (!shape) {
      if (styleLoadedRef.current) {
        clearOverlay();
        if (m.getLayer(LAYER)) m.setLayoutProperty(LAYER, "visibility", "visible");
      }
      return;
    }
    if (styleLoadedRef.current) drawOverlay();
    else m.once("style.load", drawOverlay);
  }, [shape, mapRef, styleLoadedRef, styleEpoch]);
}
