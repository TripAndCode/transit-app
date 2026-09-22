import { useEffect, useEffectEvent } from "react";
import type { LayerSpecification, Map as MLMap } from "maplibre-gl";
import type { TimelineFrame } from "../../api/types";
import { DELAY_THRESHOLDS, severityStepColors, surfaceColorResolved } from "../../styles/tokens";
import { useThemeSignal } from "../../styles/theme";
import { CROSS_FADE_MS, GHOST_OPACITY, timelineFeatures } from "./playbackFrames";
import { whenStyleReady } from "./styleReady";
import { LIVE_TRIPS_CLUSTER_LAYER, LIVE_TRIPS_LABEL_LAYER, LIVE_TRIPS_LAYER } from "./useOperationsMapLayers";

export const TIMELINE_SOURCE = "timeline";
export const TIMELINE_LAYER = "timeline-stops";

type CirclePaint = NonNullable<Extract<LayerSpecification, { type: "circle" }>["paint"]>;
/** MapLibre accepts a `<property>-transition` alongside every paint property,
 *  but the bundle's `paint` type does not declare them, so the one this layer
 *  needs is spelled out here rather than cast away at the call site. */
type TimelinePaint = CirclePaint & { "circle-opacity-transition": { duration: number; delay: number } };

/** The live layers playback stands in for. Hidden rather than removed: they are
 *  owned by another hook, which keeps feeding them, and a removed layer would
 *  have to be rebuilt from scratch on the way back. */
const LIVE_LAYERS = [LIVE_TRIPS_LAYER, LIVE_TRIPS_LABEL_LAYER, LIVE_TRIPS_CLUSTER_LAYER];

/**
 * Paint for the playback layer.
 *
 * Opacity is data-driven by `age` so one layer can carry the current frame and
 * its two ghosts; `circle-opacity-transition` is what eases a change in that
 * expression — switching in or out of the reduced-motion paint, and the
 * dimming a re-keyed feature picks up as it ages — rather than interpolating a
 * `setData` (MapLibre re-tessellates on new data; it does not tween geometry).
 * `crossFadeMs` of 0 is the reduced-motion regime: the frames step.
 *
 * Radius encodes severity, never sample count — the same rule the live vehicle
 * marks follow, so the two modes read as one map.
 */
export function timelineCirclePaint(crossFadeMs: number): TimelinePaint {
  return {
    "circle-radius": [
      "case",
      [">=", ["get", "delay_min"], DELAY_THRESHOLDS.severe], 9,
      6,
    ],
    "circle-color": ["step", ["get", "delay_min"], ...severityStepColors()],
    "circle-opacity": [
      "match", ["get", "age"], 0, GHOST_OPACITY[0], 1, GHOST_OPACITY[1], 2, GHOST_OPACITY[2], 0,
    ],
    "circle-opacity-transition": { duration: crossFadeMs, delay: 0 },
    "circle-stroke-color": surfaceColorResolved(),
    // Only the frame being played is ringed; a ghost with an edge stops being
    // a trail and starts competing for attention.
    "circle-stroke-width": ["match", ["get", "age"], 0, 1.2, 0],
  } as TimelinePaint;
}

/**
 * Drives the map's playback layer: its own `timeline` source, the live layers
 * hidden for the duration, and a pause on any camera gesture.
 *
 * Pausing on interaction is not politeness — a playhead that keeps repainting
 * while someone drags to read one corner makes the map unusable exactly when
 * they have decided what they want to look at.
 */
export function useTimelineLayers(
  mapRef: React.MutableRefObject<MLMap | null>,
  styleEpoch: number,
  frames: TimelineFrame[],
  index: number,
  active: boolean,
  steppingOnly: boolean,
  onInteract: () => void,
): void {
  const theme = useThemeSignal();
  const crossFadeMs = steppingOnly ? 0 : CROSS_FADE_MS;
  const interact = useEffectEvent(() => onInteract());

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !active) return;
    const handler = () => interact();
    const events = ["dragstart", "zoomstart", "rotatestart", "pitchstart", "mousedown", "touchstart"];
    for (const event of events) map.on(event, handler);
    return () => {
      for (const event of events) map.off(event, handler);
    };
  }, [active, mapRef]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (!active) {
      return whenStyleReady(map, () => {
        if (map.getLayer(TIMELINE_LAYER)) map.removeLayer(TIMELINE_LAYER);
        if (map.getSource(TIMELINE_SOURCE)) map.removeSource(TIMELINE_SOURCE);
        for (const id of LIVE_LAYERS) {
          if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible");
        }
      });
    }

    const data = timelineFeatures(frames, index);
    return whenStyleReady(map, () => {
      for (const id of LIVE_LAYERS) {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
      }
      const existing = map.getSource(TIMELINE_SOURCE) as { setData: (d: unknown) => void } | undefined;
      if (existing) {
        existing.setData(data);
        return;
      }
      map.addSource(TIMELINE_SOURCE, { type: "geojson", data });
      map.addLayer({
        id: TIMELINE_LAYER,
        type: "circle",
        source: TIMELINE_SOURCE,
        paint: timelineCirclePaint(crossFadeMs),
      });
    });
  }, [active, crossFadeMs, frames, index, mapRef, styleEpoch, theme]);
}
