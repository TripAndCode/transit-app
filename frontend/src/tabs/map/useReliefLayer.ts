import { useEffect, useRef } from "react";
import type { Map as MLMap } from "maplibre-gl";
import { useThemeSignal } from "../../styles/theme";
import { easeOutCamera } from "./cameraChoreography";
import { whenStyleReady } from "./styleReady";
import { repaintLayer } from "./repaintLayer";
import {
  RELIEF_LAYER,
  RELIEF_SOURCE,
  reliefFeatures,
  reliefPaint,
  sameReliefReading,
  tweenFeatures,
  type ReliefCollection,
  type ReliefPoint,
} from "./reliefLayer";
import { LIVE_TRIPS_CLUSTER_LAYER, LIVE_TRIPS_LABEL_LAYER, LIVE_TRIPS_LAYER } from "./useOperationsMapLayers";
import { TIMELINE_LAYER } from "./useTimelineLayers";

/** The live and playback layers, bottom first. The columns go under the
 *  lowest one present so the vehicle dots, clusters, labels and playback dots
 *  draw over them and keep every click and hover they had: the columns
 *  themselves are not interactive. */
const MARK_LAYERS_BOTTOM_FIRST = [LIVE_TRIPS_CLUSTER_LAYER, LIVE_TRIPS_LAYER, LIVE_TRIPS_LABEL_LAYER, TIMELINE_LAYER];

/** `setData` writes per tween. Enough for the eye to read a rise rather than
 *  a jump; few enough that the per-write cost -- serialising every column to
 *  the worker and reloading the source -- stays occasional, not per frame. */
const TWEEN_STEPS = 16;

type ReliefSource = { setData: (data: ReliefCollection) => void };

function cancelTween(rafRef: React.MutableRefObject<number | null>): void {
  if (rafRef.current == null) return;
  cancelAnimationFrame(rafRef.current);
  rafRef.current = null;
}

/**
 * Registers the relief (delay-column) layer: one GeoJSON source and one
 * `fill-extrusion` layer beside the live layers, which it never hides.
 *
 * MapLibre does not ease a paint property that reads feature data, so a new
 * reading is tweened here: `crossFadeMs` of `--ease-out` from what is on
 * screen to the new heights, in at most `TWEEN_STEPS` writes on one rAF
 * loop. A newer reading cancels the running tween and starts from wherever
 * it stopped; a re-render carrying the same reading leaves it alone.
 * `crossFadeMs` of 0 (reduced motion) is one write and no loop.
 */
export function useReliefLayer(
  mapRef: React.MutableRefObject<MLMap | null>,
  styleEpoch: number,
  on: boolean,
  points: ReliefPoint[],
  crossFadeMs: number,
): void {
  const theme = useThemeSignal();
  /** What the source holds now, mid-tween included. */
  const shownRef = useRef<ReliefCollection | null>(null);
  /** The reading the source is heading to. */
  const targetRef = useRef<ReliefCollection | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!on) {
      cancelTween(rafRef);
      shownRef.current = null;
      targetRef.current = null;
      return whenStyleReady(map, () => {
        if (map.getLayer(RELIEF_LAYER)) map.removeLayer(RELIEF_LAYER);
        if (map.getSource(RELIEF_SOURCE)) map.removeSource(RELIEF_SOURCE);
      });
    }
    const next = reliefFeatures(points);
    return whenStyleReady(map, () => {
      const source = map.getSource(RELIEF_SOURCE) as ReliefSource | undefined;
      if (!source) {
        // First show, or a style reload wiped the layer: no on-screen
        // reading to tween from.
        cancelTween(rafRef);
        map.addSource(RELIEF_SOURCE, { type: "geojson", data: next });
        const beforeId = MARK_LAYERS_BOTTOM_FIRST.find((id) => map.getLayer(id));
        map.addLayer({ id: RELIEF_LAYER, type: "fill-extrusion", source: RELIEF_SOURCE, paint: reliefPaint() }, beforeId);
        shownRef.current = next;
        targetRef.current = next;
        return;
      }
      if (targetRef.current && sameReliefReading(targetRef.current, next)) return;
      cancelTween(rafRef);
      targetRef.current = next;
      const from = shownRef.current;
      if (crossFadeMs <= 0 || !from) {
        source.setData(next);
        shownRef.current = next;
        return;
      }
      const stepMs = crossFadeMs / TWEEN_STEPS;
      let start: number | null = null;
      let lastWrite = 0;
      const tick = (now: number) => {
        // Started one step back, so the first frame already writes: a
        // reading replaced every frame (a scrub) still moves the columns.
        if (start == null) start = now - stepMs;
        const elapsed = now - start;
        const done = elapsed >= crossFadeMs;
        if (done || elapsed - lastWrite >= stepMs) {
          lastWrite = elapsed;
          const step = done ? next : tweenFeatures(from, next, easeOutCamera(elapsed / crossFadeMs));
          source.setData(step);
          shownRef.current = step;
        }
        rafRef.current = done ? null : requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
    });
  }, [crossFadeMs, mapRef, on, points, styleEpoch]);

  // Colour tokens are resolved when the paint is written, so a theme change
  // re-writes it; a reading never needs to.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !on) return;
    return whenStyleReady(map, () => {
      if (map.getLayer(RELIEF_LAYER)) repaintLayer(map, RELIEF_LAYER, reliefPaint());
    });
  }, [mapRef, on, styleEpoch, theme]);

  useEffect(() => () => cancelTween(rafRef), []);
}
