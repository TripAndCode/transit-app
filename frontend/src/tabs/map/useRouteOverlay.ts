import { useEffect } from "react";
import { type Map as MLMap } from "maplibre-gl";
import type { RouteShapeResponse, RouteShapeStop, UnobservedStop } from "../../api/types";
import { accentColorResolved, delayColorResolved, severityStepColors } from "../../styles/tokens";
import { useThemeSignal } from "../../styles/theme";
import { whenStyleReady } from "./styleReady";
import { buildTrendSegments } from "./routeTrendSegments";
import { CLUSTER_COUNT_LAYER, CLUSTER_LAYER, LAYER } from "./useHeatmapLayer";

const ROUTE_SOURCE = "route-line";
const ROUTE_STOPS_SOURCE = "route-line-stops";
const ROUTE_TREND_SOURCE = "route-trend-segments";
export const ROUTE_CASING_LAYER = "route-line-casing";
export const ROUTE_LAYER = "route-line-stroke";
export const ROUTE_TREND_LAYER = "route-trend-line";
export const ROUTE_STOPS_LAYER = "route-stops";

type RouteOverlayMode = "trend" | "hourly";

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
 * Draw the single-route overlay when `shape` is present: a white casing
 * (real GTFS road geometry when available) plus either a per-segment
 * delay-colored "trend" line (mode "trend" — shows where along the route
 * delay builds up, using the same severity scale as the stop dots) or a
 * single flat line colored by the hour-scrubber's pooled value (mode
 * "hourly"); plus one stop layer that renders observed stops as small
 * delay-colored dots and unobserved stops as hollow rings — both
 * interactive. Strips it and re-shows the agency-wide delay overlay (dots
 * + clusters + count labels) when shape isn't present. Fits bounds to the
 * route on focus.
 */
export function useRouteOverlay(
  mapRef: React.MutableRefObject<MLMap | null>,
  shape: RouteShapeResponse | undefined,
  styleEpoch: number,
  mode: RouteOverlayMode = "trend",
  scrubbedDelayMin: number | null = null,
): void {
  // Rebuild the overlay on a theme toggle so the observed-stop dots' severe
  // band re-reads the theme-aware severeColorResolved(). This effect already
  // fully rebuilds (clearOverlay + drawOverlay re-adds the source/layers), so
  // adding theme to its deps is enough — no setPaintProperty needed here.
  const theme = useThemeSignal();

  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;

    function clearOverlay() {
      if (!m) return;
      if (m.getLayer(ROUTE_STOPS_LAYER)) m.removeLayer(ROUTE_STOPS_LAYER);
      if (m.getLayer(ROUTE_LAYER)) m.removeLayer(ROUTE_LAYER);
      if (m.getLayer(ROUTE_TREND_LAYER)) m.removeLayer(ROUTE_TREND_LAYER);
      if (m.getLayer(ROUTE_CASING_LAYER)) m.removeLayer(ROUTE_CASING_LAYER);
      if (m.getSource(ROUTE_SOURCE)) m.removeSource(ROUTE_SOURCE);
      if (m.getSource(ROUTE_TREND_SOURCE)) m.removeSource(ROUTE_TREND_SOURCE);
      if (m.getSource(ROUTE_STOPS_SOURCE)) m.removeSource(ROUTE_STOPS_SOURCE);
    }

    function drawOverlay() {
      if (!m || !shape || shape.stops.length < 2) {
        clearOverlay();
        if (m) setDelayOverlayVisibility(m, "visible");
        return;
      }
      clearOverlay();
      // A route can have several observed shape variants (系統) — the
      // backend returns MultiLineString when there's more than one so all
      // of them render, not just the majority-observed one. Flatten every
      // sub-line's points for bounds-fitting below regardless of type.
      const routeGeometry: GeoJSON.LineString | GeoJSON.MultiLineString =
        shape.geometry && shape.geometry.coordinates.length >= 1
          ? shape.geometry
          : { type: "LineString", coordinates: shape.stops.map((s) => [s.lon, s.lat]) };
      const coords: [number, number][] = (
        routeGeometry.type === "MultiLineString" ? routeGeometry.coordinates.flat() : routeGeometry.coordinates
      ).map(([lon, lat]) => [lon, lat]);

      setDelayOverlayVisibility(m, "none");

      m.addSource(ROUTE_SOURCE, {
        type: "geojson",
        data: {
          type: "Feature",
          geometry: routeGeometry,
          properties: {},
        },
      });
      // White casing drawn under the colored line(s) so it stays legible
      // against basemap colors it would otherwise blend into (earth-tone
      // basemaps vs. the mild/moderate tiers of the delay ramp, both in the
      // sand/tan range). Renders in both modes — it's the "true path"
      // context layer regardless of which color story is on top.
      m.addLayer({
        id: ROUTE_CASING_LAYER,
        type: "line",
        source: ROUTE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#ffffff",
          "line-width": ["interpolate", ["linear"], ["zoom"], 8, 4, 13, 7, 17, 11],
          "line-opacity": 0.9,
        },
      });

      if (mode === "trend") {
        // Per-segment coloring: shows where along the route delay
        // accumulates, using straight stop-to-stop segments (not
        // projected onto the real road geometry — the casing above
        // already shows the true path for context; see spec's Non-goals).
        const segments = buildTrendSegments(shape.stops, shape.unobserved_stops ?? []);
        m.addSource(ROUTE_TREND_SOURCE, {
          type: "geojson",
          data: { type: "FeatureCollection", features: segments },
        });
        m.addLayer({
          id: ROUTE_TREND_LAYER,
          type: "line",
          source: ROUTE_TREND_SOURCE,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": [
              "case",
              ["get", "has_data"],
              ["step", ["get", "avg_min"], ...severityStepColors()],
              "rgba(150,150,150,0.5)",
            ],
            "line-width": ["interpolate", ["linear"], ["zoom"], 8, 2, 13, 4, 17, 7],
            "line-opacity": 1,
          },
        });
      } else {
        // Hourly mode: one flat line for the whole route, colored by the
        // scrubber's pooled value for the currently-scrubbed hour.
        m.addLayer({
          id: ROUTE_LAYER,
          type: "line",
          source: ROUTE_SOURCE,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": scrubbedDelayMin != null ? delayColorResolved(scrubbedDelayMin) : accentColorResolved(),
            "line-width": ["interpolate", ["linear"], ["zoom"], 8, 2, 13, 4, 17, 7],
            "line-opacity": 1,
          },
        });
      }

      // One feature per stop, observed and unobserved together, distinguished
      // by a `has_data` flag. A single layer means the existing route-stop
      // click/hover handlers (registered on ROUTE_STOPS_LAYER in MapTab)
      // cover every stop — unobserved ones are no longer dead, unexplained
      // markers. RouteShapeStop carries avg_min/samples; UnobservedStop
      // doesn't — accept either and only read the metrics for observed stops.
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
          // Stop dots are now a secondary reference layer (the trend
          // segments/hourly line carry the primary color story), so they're
          // deliberately smaller than before — exact values stay available
          // via the existing click/hover popup. A `zoom` expression may only
          // be used as the direct input to a top-level `step`/`interpolate`
          // — it can't be nested inside another expression (including
          // arithmetic like `*`) — so `has_data` is resolved per zoom stop
          // instead of wrapping a second interpolate.
          "circle-radius": [
            "interpolate", ["linear"], ["zoom"],
            10, ["case", ["get", "has_data"], 3, 2],
            14, ["case", ["get", "has_data"], 5, 3],
            17, ["case", ["get", "has_data"], 8, 5],
          ],
          "circle-color": [
            "case",
            ["get", "has_data"],
            ["step", ["get", "avg_min"], ...severityStepColors()],
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
    // scrubbedDelayMin is deliberately excluded: it only sets the *initial*
    // line color on a real (re)draw in hourly mode, and is kept in sync
    // afterward by the lightweight setPaintProperty effect below without
    // tearing this one down. mode IS included — switching modes swaps which
    // layer renders, which does need a full redraw.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape, mapRef, styleEpoch, theme, mode]);

  // Hour-scrub recoloring only — deliberately its own effect, not a dep of
  // the draw effect above. The draw effect fully tears down and rebuilds all
  // sources and layers (plus fitBounds); doing that on every scrub tick
  // (every ~1s during playback) caused a visible flicker of the whole
  // overlay. A color change only needs setPaintProperty on the already-drawn
  // line, and only applies in hourly mode — trend mode has no flat line.
  useEffect(() => {
    const m = mapRef.current;
    if (!m || mode !== "hourly" || !m.getLayer(ROUTE_LAYER)) return;
    const color = scrubbedDelayMin != null ? delayColorResolved(scrubbedDelayMin) : accentColorResolved();
    m.setPaintProperty(ROUTE_LAYER, "line-color", color);
  }, [scrubbedDelayMin, mode, theme, mapRef]);
}
