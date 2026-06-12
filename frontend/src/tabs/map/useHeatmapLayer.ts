import { useEffect, useRef } from "react";
import maplibregl, { type Map as MLMap } from "maplibre-gl";
import type { HeatmapCollection } from "../../api/types";
import { DELAY_RAMP, HEAT_RAMP } from "../../styles/tokens";
import type { SeverityKey } from "../../components/MapLegend";

export const SOURCE = "delays";
export const LAYER = "delay-circles";
export const HEAT_LAYER = "delay-heat";
const CASING_LAYER = "delay-casing";

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

// Fade an opacity in with zoom WITHOUT losing its data-driven value. MapLibre forbids
// `["zoom"]` nested under arithmetic, so we can't multiply a zoom factor in; instead the
// full (samples/focus-aware) expression is the STOP OUTPUT of a top-level zoom interpolate:
// 0 at overview (heatmap is showing), reaching the full value by z13.5. Mirrors DOT_RADIUS.
function zoomFadeIn(
  full: maplibregl.DataDrivenPropertyValueSpecification<number>,
): maplibregl.DataDrivenPropertyValueSpecification<number> {
  return ["interpolate", ["linear"], ["zoom"], 11, 0, 13.5, full, 18, full] as maplibregl.DataDrivenPropertyValueSpecification<number>;
}

/**
 * Sync the heatmap SOURCE / LAYER / CASING_LAYER to the latest fetched
 * GeoJSON. Filters out single-sample stops unless `showSingleSampleStops`
 * is true. Fits bounds on the first non-empty payload after each data-source
 * (`agencyId`) switch — so changing agency re-pivots to the new region, while
 * subsequent filter changes within an agency keep the user's pan/zoom.
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
      if (m.getLayer(HEAT_LAYER)) m.removeLayer(HEAT_LAYER);
      if (m.getSource(SOURCE)) m.removeSource(SOURCE);

      m.addSource(SOURCE, { type: "geojson", data: filteredSnapshot, generateId: true });

      // Overview density field: replaces the dot mass when zoomed out, fades out by
      // z14 as the dots fade in. Weighted by delay severity (low-delay barely paints)
      // and kept low-intensity/small-radius so it shows hotspots, not a red blanket.
      const heatWeightExpr: maplibregl.ExpressionSpecification = [
        "interpolate", ["linear"], ["get", "avg_delay_min"],
        0, 0, 2, 0.22, 5, 0.7, 10, 1,
      ];
      const heatIntensityExpr: maplibregl.ExpressionSpecification = [
        "interpolate", ["linear"], ["zoom"], 8, 0.25, 11, 0.5, 13, 0.8,
      ];
      const heatRadiusExpr: maplibregl.ExpressionSpecification = [
        "interpolate", ["linear"], ["zoom"], 8, 9, 11, 15, 13, 22,
      ];
      const heatOpacityExpr: maplibregl.ExpressionSpecification = [
        "interpolate", ["linear"], ["zoom"], 11, 0.85, 13, 0.55, 14, 0,
      ];
      const heatColorExpr: maplibregl.ExpressionSpecification = [
        "interpolate", ["linear"], ["heatmap-density"],
        ...(HEAT_RAMP.flatMap((s) => [s[0], s[1]]) as (number | string)[]),
      ];
      m.addLayer({
        id: HEAT_LAYER,
        type: "heatmap",
        source: SOURCE,
        maxzoom: 15,
        paint: {
          "heatmap-weight": heatWeightExpr,
          "heatmap-intensity": heatIntensityExpr,
          "heatmap-radius": heatRadiusExpr,
          "heatmap-opacity": heatOpacityExpr,
          "heatmap-color": heatColorExpr,
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
        paint: {
          "circle-radius": DOT_RADIUS,
          "circle-color": "rgba(0,0,0,0)",
          "circle-stroke-color": "#0f1115",
          "circle-stroke-width": [
            "case", ["boolean", ["feature-state", "hover"], false], 7, 5,
          ],
          "circle-stroke-opacity": zoomFadeIn(buildCasingOpacityExpr(focusedSeverity)),
          "circle-pitch-alignment": "map",
        },
      });

      m.addLayer({
        id: LAYER,
        type: "circle",
        source: SOURCE,
        paint: {
          "circle-radius": DOT_RADIUS,
          "circle-color": colorExpr,
          "circle-opacity": zoomFadeIn(buildCircleOpacityExpr(focusedSeverity)),
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

    if (styleLoadedRef.current) applyData();
    else m.once("style.load", applyData);
  }, [data, showSingleSampleStops, focusedSeverity, mapRef, styleLoadedRef, styleEpoch]);
}
