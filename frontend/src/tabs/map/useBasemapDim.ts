import { useEffect } from "react";
import { type Map as MLMap, type ExpressionSpecification } from "maplibre-gl";
import { whenStyleReady } from "./styleReady";

export const SCRIM_LAYER = "basemap-scrim";
const BASEMAP_LAYER = "basemap";

// Zoom-gated mute: none at overview (the heatmap owns it and basemap context is
// useful), ramping in over [startZoom, 14] -- the same 14 the detail dots fade
// in at -- so dots/route pop without a louder basemap. Paint props on a raster
// layer accept zoom expressions.
//
// startZoom is 12 for the default heatmap-dot view (unchanged from the
// original tuning) but widens to 6 in route mode: a focused route is often
// viewed much more zoomed out than the heatmap's per-stop detail view, and at
// zoom < 12 the ramp was fully inactive (value 0), so the route line's
// severity colors competed with a fully-saturated basemap (measured ~2.1:1
// contrast for the "ok" green against typical OSM land-green -- under the
// WCAG 3:1 floor for meaningful graphics).
// Each function's literal is the mute at dimAmount = 1 (full strength — the
// only strength this hook offered before it gained a caller-supplied
// amount). Scaling linearly by dimAmount makes 0 a true no-op mute and any
// intermediate amount interpolate smoothly between it and this design.
function dimSaturation(startZoom: number, dimAmount: number): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], startZoom, 0, 14, -0.5 * dimAmount];
}
function dimContrast(startZoom: number, dimAmount: number): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], startZoom, 0, 14, -0.12 * dimAmount];
}
function dimBrightnessMax(startZoom: number, dimAmount: number): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], startZoom, 1, 14, 1 - 0.08 * dimAmount];
}
function scrimOpacity(startZoom: number, dimAmount: number): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], startZoom, 0, 14, 0.2 * dimAmount];
}

/**
 * Mute the basemap so the POI/heatmap layer (or, in route mode, the route
 * overlay line) owns the contrast at detail zoom (the standard data-overlay
 * treatment). Desaturates + slightly darkens the `basemap` raster and lays a
 * faint white scrim directly above it — but BELOW the overlay layers, which
 * is why MapTab calls this hook before the overlay hooks (effect order =
 * call order). Re-applies on each `styleEpoch` bump because `setStyle` wipes
 * the paint overrides and the scrim.
 *
 * `isRouteMode` (default false) widens the zoom range the ramp is active
 * over — see the startZoom comment above the helper functions.
 *
 * `dimAmount` (default 1, full strength) scales how strong the mute gets at
 * zoom 14 — the user-facing "basemap dim" slider passes its current value
 * here. 0 disables the mute entirely without changing the zoom range.
 */
export function useBasemapDim(
  mapRef: React.MutableRefObject<MLMap | null>,
  styleEpoch: number,
  isRouteMode = false,
  dimAmount = 1,
): void {
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    const startZoom = isRouteMode ? 6 : 12;

    function apply() {
      if (!m || !m.getLayer(BASEMAP_LAYER)) return;
      m.setPaintProperty(BASEMAP_LAYER, "raster-saturation", dimSaturation(startZoom, dimAmount));
      m.setPaintProperty(BASEMAP_LAYER, "raster-contrast", dimContrast(startZoom, dimAmount));
      m.setPaintProperty(BASEMAP_LAYER, "raster-brightness-max", dimBrightnessMax(startZoom, dimAmount));
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
            paint: { "background-color": "#ffffff", "background-opacity": scrimOpacity(startZoom, dimAmount) },
          },
          before,
        );
      } else {
        m.setPaintProperty(SCRIM_LAYER, "background-opacity", scrimOpacity(startZoom, dimAmount));
      }
    }

    // Re-attach when the style is fully ready (re-arms on `styledata`, not the
    // one-shot `style.load`) so the scrim survives a basemap/language reload
    // even when basemap tiles finish after style.load fires.
    return whenStyleReady(m, apply);
  }, [mapRef, styleEpoch, isRouteMode, dimAmount]);
}
