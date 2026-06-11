import { useEffect, useRef } from "react";
import maplibregl, { type Map as MLMap } from "maplibre-gl";
import type { HeatmapCollection } from "../../api/types";
import { DELAY_RAMP } from "../../styles/tokens";
import type { SeverityKey } from "../../components/MapLegend";

export const SOURCE = "delays";
export const LAYER = "delay-circles";
const HALO_LAYER = "delay-halos";

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

function buildHaloOpacityExpr(
  focused: SeverityKey | null,
): maplibregl.DataDrivenPropertyValueSpecification<number> {
  const base: maplibregl.DataDrivenPropertyValueSpecification<number> = [
    "max",
    [
      "case",
      [">=", ["get", "avg_delay_min"], 10], 0.20,
      [">=", ["get", "avg_delay_min"], 5], 0.14,
      0.0,
    ],
    [
      "interpolate", ["exponential", 1.4], ["get", "samples"],
      10, 0.06,
      1000, 0.10,
      50000, 0.16,
    ],
  ];
  if (focused === null) return base;
  return ["case", severityMatchExpr(focused), base, 0];
}

/**
 * Sync the heatmap SOURCE / LAYER / HALO_LAYER to the latest fetched
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
      if (m.getLayer(HALO_LAYER)) m.removeLayer(HALO_LAYER);
      if (m.getSource(SOURCE)) m.removeSource(SOURCE);

      m.addSource(SOURCE, { type: "geojson", data: filteredSnapshot, generateId: true });

      const colorExpr: maplibregl.ExpressionSpecification = [
        "step",
        ["get", "avg_delay_min"],
        DELAY_RAMP.ok,
        2, DELAY_RAMP.mild,
        5, DELAY_RAMP.moderate,
        10, DELAY_RAMP.severe,
      ];

      const HALO_RADIUS: maplibregl.ExpressionSpecification = [
        "interpolate", ["exponential", 1.4], ["get", "samples"],
        10, 6,
        100, 10,
        1000, 16,
        10000, 26,
        50000, 36,
      ];
      const DOT_RADIUS: maplibregl.ExpressionSpecification = [
        "interpolate", ["exponential", 1.4], ["get", "samples"],
        10, 4,
        100, 6,
        1000, 7,
        10000, 12,
        50000, 18,
      ];

      m.addLayer({
        id: HALO_LAYER,
        type: "circle",
        source: SOURCE,
        paint: {
          "circle-radius": HALO_RADIUS,
          "circle-color": colorExpr,
          "circle-blur": 0.5,
          "circle-opacity": buildHaloOpacityExpr(focusedSeverity),
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
          "circle-opacity": buildCircleOpacityExpr(focusedSeverity),
          "circle-stroke-width": [
            "case", ["boolean", ["feature-state", "hover"], false], 2, 1,
          ],
          "circle-stroke-color": [
            "case", ["boolean", ["feature-state", "hover"], false],
            "#ffffff", "rgba(0,0,0,0.35)",
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
    else m.once("load", applyData);
  }, [data, showSingleSampleStops, focusedSeverity, mapRef, styleLoadedRef, styleEpoch]);
}
