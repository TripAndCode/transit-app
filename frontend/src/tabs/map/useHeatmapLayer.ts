import { useEffect, useRef } from "react";
import maplibregl, { type Map as MLMap } from "maplibre-gl";
import type { HeatmapCollection } from "../../api/types";
import { whenStyleReady } from "./styleReady";
import { DELAY_RAMP, severeColorResolved } from "../../styles/tokens";
import { useThemeSignal } from "../../styles/theme";
import type { SeverityKey } from "../../components/MapLegend";

export const SOURCE = "delays";
export const LAYER = "delay-circles";
export const CLUSTER_LAYER = "delay-clusters";
export const CLUSTER_COUNT_LAYER = "delay-cluster-count";
const CASING_LAYER = "delay-casing";

// Stops within ~50px collapse into one bubble until this zoom; past it they
// render individually as the bullseye dots. Kept in sync with the muted-basemap
// ramp (useBasemapDim, z12->14) so the basemap quiets as the dots take over.
const CLUSTER_RADIUS = 50;
const CLUSTER_MAX_ZOOM = 13;
// Selects clustered vs. unclustered features. Clusters carry `point_count`;
// individual stops do not.
const IS_CLUSTER: maplibregl.ExpressionSpecification = ["has", "point_count"];
const IS_STOP: maplibregl.ExpressionSpecification = ["!", ["has", "point_count"]];

// Solid fills — delay is encoded by color + dot SIZE, not by opacity, and a
// focused band now FILTERS the data (below) rather than dimming non-matching
// dots, so these are plain constants.
const DOT_OPACITY = 0.92;
const CASING_OPACITY = 0.85;

/**
 * JS predicate mirroring the legend's delay bands. Used to FILTER the stops fed
 * to the (clustered) source when a band is focused: dimming non-matching dots
 * stopped working once stops were clustered into bubbles, so instead we drop the
 * non-matching stops entirely and let the clusters + dots re-form from the rest.
 */
function inSeverityBand(avg: number, band: SeverityKey): boolean {
  switch (band) {
    case "ok":
      return avg < 2;
    case "mild":
      return avg >= 2 && avg < 5;
    case "moderate":
      return avg >= 5 && avg < 10;
    case "severe":
      return avg >= 10;
  }
}

/**
 * Sync the clustered SOURCE + its layers to the latest fetched GeoJSON:
 *  - `CLUSTER_LAYER` — one bubble per cluster at overview (color = average
 *    delay of its stops, size = stop count). Clicking it zooms in (handled in
 *    MapTab via `getClusterExpansionZoom`).
 *  - `CASING_LAYER` + `LAYER` — the bullseye dot for each individual stop once
 *    it unclusters at detail zoom (filtered to non-cluster features).
 * Filters out single-sample stops unless `showSingleSampleStops` is true. Fits
 * bounds on the first non-empty payload after each data-source (`agencyId`)
 * switch — so changing agency re-pivots to the new region, while subsequent
 * filter changes within an agency keep the user's pan/zoom.
 */
export function useHeatmapLayer(
  mapRef: React.MutableRefObject<MLMap | null>,
  data: HeatmapCollection | undefined,
  showSingleSampleStops: boolean,
  focusedSeverity: SeverityKey | null,
  agencyId: number | null,
  styleEpoch: number,
  colorField: 'avg_delay_min' | 'p90_delay_min' = 'avg_delay_min',
): void {
  const fittedRef = useRef(false);
  // Re-render signal on theme toggle: severeColorResolved() reads the theme-aware
  // --delay-severe CSS var, but a MapLibre paint expression is a plain JS value
  // captured at build time — the browser cascade can't reach into it. Depending
  // on this in the recolor effect below rebuilds the color expressions so the
  // severe band tracks the theme without a data refresh.
  const theme = useThemeSignal();

  // Re-arm the bounds fit when the data source changes. Without this the
  // camera latches on the first agency ever shown (e.g. Hiroshima) and never
  // re-pivots when the user switches to another (e.g. Aomori).
  useEffect(() => {
    fittedRef.current = false;
  }, [agencyId]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m || !data) return;
    // Filter the stops once, in JS: drop single-sample stops (unless shown) and,
    // when a legend band is focused, drop everything outside that band so the
    // clusters re-form from only the matching stops.
    const features = data.features.filter((f) => {
      const p = f.properties ?? {};
      if (!showSingleSampleStops && (p.samples ?? 0) < 2) return false;
      if (focusedSeverity && !inSeverityBand(p.avg_delay_min ?? 0, focusedSeverity)) return false;
      return true;
    });
    const filteredSnapshot = { ...data, features };

    function applyData() {
      if (!m) return;

      // Flicker-free path: if the source already exists (a data/filter change on
      // the same style), just swap its data — clustering + the layers re-render
      // in place. Rebuilding source+layers on every filter toggle caused a
      // visible flash. The source is only (re)built on first attach or after a
      // basemap switch wiped it (styleEpoch).
      const existing = m.getSource(SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (existing) {
        existing.setData(filteredSnapshot);
      } else {
        buildLayers();
      }

      if (!fittedRef.current) fitToData();
    }

    function buildLayers() {
      if (!m) return;
      m.addSource(SOURCE, {
        type: "geojson",
        data: filteredSnapshot,
        generateId: true,
        cluster: true,
        clusterRadius: CLUSTER_RADIUS,
        clusterMaxZoom: CLUSTER_MAX_ZOOM,
        // Sum the member delays so the bubble can color by their average
        // (dsum / point_count). MapLibre has no built-in average accumulator.
        clusterProperties: { dsum: ["+", ["get", "avg_delay_min"]] },
      });

      // Overview bubble: COLOR = average delay of the cluster's stops (the
      // signal — "is it delayed here?"), SIZE = how many stops. No count label
      // (color carries the message; a glyph source would be an extra dep) — the
      // exact count + stops live in the click popup, and clicking zooms in.
      const clusterAvgDelay: maplibregl.ExpressionSpecification = [
        "/", ["get", "dsum"], ["get", "point_count"],
      ];
      const clusterColor: maplibregl.ExpressionSpecification = [
        "step", clusterAvgDelay,
        DELAY_RAMP.ok,
        2, DELAY_RAMP.mild,
        5, DELAY_RAMP.moderate,
        10, severeColorResolved(),
      ];
      const clusterRadius: maplibregl.ExpressionSpecification = [
        "step", ["get", "point_count"], 15, 10, 19, 50, 25, 200, 32,
      ];
      m.addLayer({
        id: CLUSTER_LAYER,
        type: "circle",
        source: SOURCE,
        filter: IS_CLUSTER,
        paint: {
          "circle-color": clusterColor,
          "circle-radius": clusterRadius,
          "circle-stroke-width": 2.5,
          "circle-stroke-color": "#ffffff",
          "circle-opacity": 0.95,
        },
      });

      // Stop-count label on each bubble — an empty colored circle reads as
      // "creepy"; the number tells the user how many stops collapsed here.
      m.addLayer({
        id: CLUSTER_COUNT_LAYER,
        type: "symbol",
        source: SOURCE,
        filter: IS_CLUSTER,
        layout: {
          "text-field": ["get", "point_count_abbreviated"],
          "text-font": ["Noto Sans Regular"],
          "text-size": 13,
          "text-allow-overlap": true,
        },
        paint: {
          "text-color": "#ffffff",
          "text-halo-color": "rgba(0,0,0,0.25)",
          "text-halo-width": 1,
        },
      });

      const colorExpr: maplibregl.ExpressionSpecification = [
        "step",
        ["get", colorField],
        DELAY_RAMP.ok,
        2, DELAY_RAMP.mild,
        5, DELAY_RAMP.moderate,
        10, severeColorResolved(),
      ];

      // Dot SIZE encodes the DELAY itself — bigger = worse — so color and size
      // reinforce the same signal. (Size used to scale with sample count, which
      // just made data-rich stops loom large regardless of delay.) MapLibre
      // requires `zoom` to be the TOP-LEVEL interpolate input, so each per-zoom
      // stop output is the delay-driven size scaled by a per-zoom factor.
      const delaySize = (k: number): maplibregl.ExpressionSpecification => [
        "interpolate", ["linear"], ["get", colorField],
        0, 4 * k,
        2, 6 * k,
        5, 9 * k,
        10, 13 * k,
      ];
      const DOT_RADIUS: maplibregl.ExpressionSpecification = [
        "interpolate", ["linear"], ["zoom"],
        8, delaySize(0.5),
        11, delaySize(0.85),
        13, delaySize(1.1),
        16, delaySize(1.6),
        18, delaySize(2.1),
      ];

      // Dark casing ring drawn UNDER the dot: a transparent-fill circle at the
      // dot radius with a wide dark stroke, so only a dark ring shows around
      // the dot's perimeter (no dark disc behind the fill to muddy the color).
      // Pairs with the dot's white inner stroke for legibility on any basemap.
      m.addLayer({
        id: CASING_LAYER,
        type: "circle",
        source: SOURCE,
        filter: IS_STOP,
        paint: {
          "circle-radius": DOT_RADIUS,
          "circle-color": "rgba(0,0,0,0)",
          "circle-stroke-color": "#0f1115",
          "circle-stroke-width": [
            "case", ["boolean", ["feature-state", "hover"], false], 7, 5,
          ],
          "circle-stroke-opacity": CASING_OPACITY,
          "circle-pitch-alignment": "map",
        },
      });

      m.addLayer({
        id: LAYER,
        type: "circle",
        source: SOURCE,
        filter: IS_STOP,
        paint: {
          "circle-radius": DOT_RADIUS,
          "circle-color": colorExpr,
          "circle-opacity": DOT_OPACITY,
          // White stroke reads against ANY basemap — light (淡色), busy/warm
          // (OSM, 標準), and dark imagery (航空写真) — where the old faint dark
          // stroke vanished. Thickens on hover for emphasis.
          "circle-stroke-width": [
            "case", ["boolean", ["feature-state", "hover"], false], 3, 1.5,
          ],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-opacity": [
            "case", ["boolean", ["feature-state", "hover"], false], 1, 0.9,
          ],
        },
      });

    }

    // Fit the camera to the data on the first non-empty payload after an agency
    // switch (fittedRef re-armed elsewhere). Runs for both the build and setData
    // paths so switching agency re-pivots; subsequent filter toggles keep the
    // user's pan/zoom because fittedRef is already set.
    function fitToData() {
      if (!m) return;
      if (filteredSnapshot.features.length === 1) {
        const [lon, lat] = filteredSnapshot.features[0].geometry.coordinates;
        m.flyTo({ center: [lon, lat], zoom: 13, duration: 600 });
        fittedRef.current = true;
      } else if (filteredSnapshot.features.length > 1) {
        let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
        for (const f of filteredSnapshot.features) {
          const [lon, lat] = f.geometry.coordinates;
          if (lon < minLon) minLon = lon;
          if (lon > maxLon) maxLon = lon;
          if (lat < minLat) minLat = lat;
          if (lat > maxLat) maxLat = lat;
        }
        m.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 40, duration: 600 });
        fittedRef.current = true;
      }
    }

    // Re-attach when the style is fully ready. whenStyleReady re-arms on
    // `styledata` rather than the one-shot `style.load`, so a re-run that lands
    // after style.load but before basemap tiles finish (isStyleLoaded() false)
    // still attaches instead of waiting on an event that won't fire again.
    return whenStyleReady(m, applyData);
  }, [data, showSingleSampleStops, focusedSeverity, mapRef, styleEpoch, colorField]);

  // Update color when colorField OR the theme changes, without rebuilding the
  // whole layer (setPaintProperty, not addLayer — leaves the build/style-race
  // path in the effect above untouched). Only runs after the layer setup effect
  // has fired (layers exist on map). Both the dots and the cluster bubbles embed
  // severeColorResolved(), so both are re-read here on a theme toggle.
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    if (!m.getLayer(LAYER)) return;
    const expr: maplibregl.ExpressionSpecification = [
      "step", ["get", colorField],
      DELAY_RAMP.ok, 2, DELAY_RAMP.mild, 5, DELAY_RAMP.moderate, 10, severeColorResolved(),
    ];
    m.setPaintProperty(LAYER, "circle-color", expr);
    if (m.getLayer(CLUSTER_LAYER)) {
      const clusterColor: maplibregl.ExpressionSpecification = [
        "step", ["/", ["get", "dsum"], ["get", "point_count"]],
        DELAY_RAMP.ok, 2, DELAY_RAMP.mild, 5, DELAY_RAMP.moderate, 10, severeColorResolved(),
      ];
      m.setPaintProperty(CLUSTER_LAYER, "circle-color", clusterColor);
    }
  }, [mapRef, colorField, theme]);
}
