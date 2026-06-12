import { useEffect, useRef } from "react";
import maplibregl, { type Map as MLMap } from "maplibre-gl";
import type { HeatmapCollection } from "../../api/types";
import { DELAY_RAMP } from "../../styles/tokens";
import type { SeverityKey } from "../../components/MapLegend";

export const SOURCE = "delays";
export const LAYER = "delay-circles";
export const CLUSTER_LAYER = "delay-clusters";
const CLUSTER_COUNT_LAYER = "delay-cluster-count";
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

/**
 * Build the MapLibre filter expression that selects circles falling into a
 * single severity band.
 */
function severityMatchExpr(focused: SeverityKey): maplibregl.ExpressionSpecification {
  switch (focused) {
    case "ok":
      return ["<", ["get", "avg_delay_min"], 2];
    case "mild":
      return ["all", [">=", ["get", "avg_delay_min"], 2], ["<", ["get", "avg_delay_min"], 5]];
    case "moderate":
      return ["all", [">=", ["get", "avg_delay_min"], 5], ["<", ["get", "avg_delay_min"], 10]];
    case "severe":
      return [">=", ["get", "avg_delay_min"], 10];
  }
}

function buildCircleOpacityExpr(
  focused: SeverityKey | null,
): maplibregl.DataDrivenPropertyValueSpecification<number> {
  const base: maplibregl.DataDrivenPropertyValueSpecification<number> = [
    "max",
    [
      "case",
      [">=", ["get", "avg_delay_min"], 10], 0.7,
      [">=", ["get", "avg_delay_min"], 5], 0.55,
      0.0,
    ],
    [
      "interpolate", ["linear"], ["get", "samples"],
      1, 0.35,
      50, 0.7,
      500, 0.85,
    ],
  ];
  if (focused === null) return base;
  return ["case", severityMatchExpr(focused), base, 0];
}

// Dark casing ring opacity. Constant + focus-aware (non-focused dim to 0 so a
// dimmed dot's ring fades with it). Gives every dot a dark outer ring that —
// together with the dot's white inner stroke — reads on any basemap (white
// separates on dark/satellite, dark separates on light/busy).
function buildCasingOpacityExpr(
  focused: SeverityKey | null,
): maplibregl.DataDrivenPropertyValueSpecification<number> {
  const base = 0.85;
  if (focused === null) return base;
  return ["case", severityMatchExpr(focused), base, 0];
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
  styleLoadedRef: React.MutableRefObject<boolean>,
  data: HeatmapCollection | undefined,
  showSingleSampleStops: boolean,
  focusedSeverity: SeverityKey | null,
  agencyId: number | null,
  styleEpoch: number,
): void {
  const fittedRef = useRef(false);

  // Re-arm the bounds fit when the data source changes. Without this the
  // camera latches on the first agency ever shown (e.g. Hiroshima) and never
  // re-pivots when the user switches to another (e.g. Aomori).
  useEffect(() => {
    fittedRef.current = false;
  }, [agencyId]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m || !data) return;
    const filteredSnapshot = showSingleSampleStops
      ? data
      : {
          ...data,
          features: data.features.filter((f) => (f.properties?.samples ?? 0) >= 2),
        };

    function applyData() {
      if (!m) return;
      if (m.getLayer(LAYER)) m.removeLayer(LAYER);
      if (m.getLayer(CASING_LAYER)) m.removeLayer(CASING_LAYER);
      if (m.getLayer(CLUSTER_COUNT_LAYER)) m.removeLayer(CLUSTER_COUNT_LAYER);
      if (m.getLayer(CLUSTER_LAYER)) m.removeLayer(CLUSTER_LAYER);
      if (m.getSource(SOURCE)) m.removeSource(SOURCE);

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
        10, DELAY_RAMP.severe,
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
        ["get", "avg_delay_min"],
        DELAY_RAMP.ok,
        2, DELAY_RAMP.mild,
        5, DELAY_RAMP.moderate,
        10, DELAY_RAMP.severe,
      ];

      // Dots scale with BOTH sample count and zoom so they stay legible when
      // zoomed out (smaller, less overlap into a blob) and prominent when
      // zoomed in against the detailed basemap. MapLibre requires `zoom` to be
      // the TOP-LEVEL interpolate input, so the per-zoom stop outputs are the
      // samples-driven expression scaled by a per-zoom factor.
      const samplesDot = (k: number): maplibregl.ExpressionSpecification => [
        "interpolate", ["exponential", 1.4], ["get", "samples"],
        10, 4 * k,
        100, 6 * k,
        1000, 7 * k,
        10000, 12 * k,
        50000, 18 * k,
      ];
      const DOT_RADIUS: maplibregl.ExpressionSpecification = [
        "interpolate", ["linear"], ["zoom"],
        8, samplesDot(0.5),
        11, samplesDot(0.85),
        13, samplesDot(1.1),
        16, samplesDot(1.6),
        18, samplesDot(2.1),
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
          "circle-stroke-opacity": buildCasingOpacityExpr(focusedSeverity),
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
          "circle-opacity": buildCircleOpacityExpr(focusedSeverity),
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

      if (!fittedRef.current) {
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
    }

    // Guard on the map's own `isStyleLoaded()`, NOT the styleLoadedRef boolean:
    // this effect re-runs when `data` arrives, which can be AFTER `style.load`
    // already fired (e.g. slow basemap tiles delay the `load` event that sets
    // the ref). Registering `once("style.load")` then would wait for an event
    // that never fires again — the overlay would silently never attach.
    if (m.isStyleLoaded()) applyData();
    else m.once("style.load", applyData);
  }, [data, showSingleSampleStops, focusedSeverity, mapRef, styleLoadedRef, styleEpoch]);
}
