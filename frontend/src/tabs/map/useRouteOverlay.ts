import { useEffect } from "react";
import { type Map as MLMap } from "maplibre-gl";
import type { RouteShapeResponse, RouteShapeStop, UnobservedStop } from "../../api/types";
import { DELAY_RAMP } from "../../styles/tokens";
import { whenStyleReady } from "./styleReady";
import { CLUSTER_COUNT_LAYER, CLUSTER_LAYER, LAYER } from "./useHeatmapLayer";

const ROUTE_SOURCE = "route-line";
const ROUTE_STOPS_SOURCE = "route-line-stops";
const ROUTE_LAYER = "route-line-stroke";
export const ROUTE_STOPS_LAYER = "route-stops";

// Every layer of the agency-wide delay overlay (dots + cluster bubbles + their
// count labels). Single-route mode hides ALL of them, not just the dots —
// otherwise the whole agency's cluster bubbles bleed over the focused route.
const DELAY_OVERLAY_LAYERS = [LAYER, CLUSTER_LAYER, CLUSTER_COUNT_LAYER];

function setDelayOverlayVisibility(m: MLMap, visibility: "visible" | "none") {
  for (const id of DELAY_OVERLAY_LAYERS) {
    if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", visibility);
  }
}

/**
 * Draw the single-route overlay when `shape` is present (polyline + one stop
 * layer that renders observed stops as delay-colored dots and unobserved stops
 * as hollow rings — both interactive); strip it and re-show the agency-wide
 * delay overlay (dots + clusters + count labels) when it isn't. Fits bounds to
 * the route on focus.
 */
export function useRouteOverlay(
  mapRef: React.MutableRefObject<MLMap | null>,
  shape: RouteShapeResponse | undefined,
  styleEpoch: number,
): void {
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;

    function clearOverlay() {
      if (!m) return;
      if (m.getLayer(ROUTE_STOPS_LAYER)) m.removeLayer(ROUTE_STOPS_LAYER);
      if (m.getLayer(ROUTE_LAYER)) m.removeLayer(ROUTE_LAYER);
      if (m.getSource(ROUTE_SOURCE)) m.removeSource(ROUTE_SOURCE);
      if (m.getSource(ROUTE_STOPS_SOURCE)) m.removeSource(ROUTE_STOPS_SOURCE);
    }

    function drawOverlay() {
      if (!m || !shape || shape.stops.length < 2) {
        clearOverlay();
        if (m) setDelayOverlayVisibility(m, "visible");
        return;
      }
      clearOverlay();
      const geomCoords = shape.geometry?.coordinates;
      const coords: [number, number][] =
        geomCoords && geomCoords.length >= 2
          ? (geomCoords as [number, number][])
          : shape.stops.map((s) => [s.lon, s.lat]);

      setDelayOverlayVisibility(m, "none");

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

      // One feature per stop, observed and unobserved together, distinguished by
      // a `has_data` flag. A single layer means the existing route-stop click /
      // hover handlers (registered on ROUTE_STOPS_LAYER in MapTab) cover every
      // stop — unobserved ones are no longer dead, unexplained markers.
      // RouteShapeStop carries avg_min/samples; UnobservedStop doesn't — accept
      // either and only read the metrics for observed stops.
      const toFeature = (s: RouteShapeStop | UnobservedStop, hasData: boolean) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [s.lon, s.lat] },
        properties: {
          stop_sequence: s.stop_sequence,
          stop_name: s.stop_name,
          stop_id: s.stop_id ?? null,
          stop_code: s.stop_code ?? null,
          platform_code: s.platform_code ?? null,
          avg_min: hasData && "avg_min" in s ? (s.avg_min ?? 0) : 0,
          samples: hasData && "samples" in s ? s.samples : 0,
          has_data: hasData,
        },
      });
      const features = [
        ...shape.stops.map((s) => toFeature(s, (s.samples ?? 0) > 0)),
        ...(shape.unobserved_stops ?? []).map((s) => toFeature(s, false)),
      ];

      m.addSource(ROUTE_STOPS_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features },
      });
      m.addLayer({
        id: ROUTE_STOPS_LAYER,
        type: "circle",
        source: ROUTE_STOPS_SOURCE,
        paint: {
          // Observed stops are larger, delay-colored, white-ringed; unobserved
          // stops are smaller hollow gray rings (transparent fill).
          "circle-radius": [
            "case",
            ["get", "has_data"],
            ["interpolate", ["linear"], ["zoom"], 10, 4, 14, 7, 17, 11],
            ["interpolate", ["linear"], ["zoom"], 10, 2.5, 14, 4, 17, 6],
          ],
          "circle-color": [
            "case",
            ["get", "has_data"],
            ["step", ["get", "avg_min"], DELAY_RAMP.ok, 2, DELAY_RAMP.mild, 5, DELAY_RAMP.moderate, 10, DELAY_RAMP.severe],
            "rgba(255,255,255,0)",
          ],
          "circle-opacity": 0.95,
          "circle-stroke-width": ["case", ["get", "has_data"], 2, 1],
          "circle-stroke-color": ["case", ["get", "has_data"], "#ffffff", "rgba(0,0,0,0.35)"],
        },
      });

      let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
      for (const [lon, lat] of coords) {
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
      }
      m.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 60, duration: 600 });
    }

    // Re-attach when the style is fully ready (re-arms on `styledata`, not the
    // one-shot `style.load`) so a re-run after style.load but before basemap
    // tiles finish still applies instead of waiting on an event that won't fire.
    if (!shape) {
      return whenStyleReady(m, () => {
        clearOverlay();
        setDelayOverlayVisibility(m, "visible");
      });
    }
    return whenStyleReady(m, drawOverlay);
  }, [shape, mapRef, styleEpoch]);
}
