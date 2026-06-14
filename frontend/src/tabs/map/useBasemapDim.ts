import { useEffect } from "react";
import { type Map as MLMap, type ExpressionSpecification } from "maplibre-gl";
import { whenStyleReady } from "./styleReady";

export const SCRIM_LAYER = "basemap-scrim";
const BASEMAP_LAYER = "basemap";

// Zoom-gated mute: none at overview (the heatmap owns it and basemap context is
// useful), ramping in z12->14 — the same window the detail dots fade in — so dots
// pop without louder dots. Paint props on a raster layer accept zoom expressions.
const DIM_SATURATION: ExpressionSpecification = [
  "interpolate", ["linear"], ["zoom"], 12, 0, 14, -0.5,
];
const DIM_CONTRAST: ExpressionSpecification = [
  "interpolate", ["linear"], ["zoom"], 12, 0, 14, -0.12,
];
const DIM_BRIGHTNESS_MAX: ExpressionSpecification = [
  "interpolate", ["linear"], ["zoom"], 12, 1, 14, 0.92,
];
const SCRIM_OPACITY: ExpressionSpecification = [
  "interpolate", ["linear"], ["zoom"], 12, 0, 14, 0.2,
];

/**
 * Mute the basemap so the POI/heatmap layer owns the contrast at detail zoom
 * (the standard data-overlay treatment). Desaturates + slightly darkens the
 * `basemap` raster and lays a faint white scrim directly above it — but BELOW
 * the overlay layers, which is why MapTab calls this hook before the overlay
 * hooks (effect order = call order). Re-applies on each `styleEpoch` bump because
 * `setStyle` wipes the paint overrides and the scrim.
 */
export function useBasemapDim(
  mapRef: React.MutableRefObject<MLMap | null>,
  styleLoadedRef: React.MutableRefObject<boolean>,
  styleEpoch: number,
): void {
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;

    function apply() {
      if (!m || !m.getLayer(BASEMAP_LAYER)) return;
      m.setPaintProperty(BASEMAP_LAYER, "raster-saturation", DIM_SATURATION);
      m.setPaintProperty(BASEMAP_LAYER, "raster-contrast", DIM_CONTRAST);
      m.setPaintProperty(BASEMAP_LAYER, "raster-brightness-max", DIM_BRIGHTNESS_MAX);
      if (!m.getLayer(SCRIM_LAYER)) {
        // First non-basemap layer = the lowest overlay (if any yet). Insert the
        // scrim before it so it sits ABOVE basemap but BELOW the overlay; if no
        // overlay exists yet (freshly reloaded style) it appends just above
        // basemap and the overlay layers added afterwards land on top.
        const before = m
          .getStyle()
          .layers.map((l) => l.id)
          .find((id) => id !== BASEMAP_LAYER && id !== SCRIM_LAYER);
        m.addLayer(
          {
            id: SCRIM_LAYER,
            type: "background",
            paint: { "background-color": "#ffffff", "background-opacity": SCRIM_OPACITY },
          },
          before,
        );
      }
    }

    // Re-attach when the style is fully ready (re-arms on `styledata`, not the
    // one-shot `style.load`) so the scrim survives a basemap/language reload
    // even when basemap tiles finish after style.load fires.
    return whenStyleReady(m, apply);
  }, [mapRef, styleLoadedRef, styleEpoch]);
}
