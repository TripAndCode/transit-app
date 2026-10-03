import { useEffect } from "react";
import type { Map as MLMap } from "maplibre-gl";
import { useThemeSignal } from "../../styles/theme";
import { whenStyleReady } from "./styleReady";
import { repaintLayer } from "./repaintLayer";
import { RELIEF_LAYER, RELIEF_SOURCE, reliefFeatures, reliefPaint, type ReliefPoint } from "./reliefLayer";
import {
  LIVE_TRIPS_CLUSTER_COUNT_LAYER,
  LIVE_TRIPS_CLUSTER_LAYER,
  LIVE_TRIPS_LABEL_LAYER,
  LIVE_TRIPS_LAYER,
} from "./useOperationsMapLayers";
import { TIMELINE_LAYER } from "./useTimelineLayers";

/** The circle layers (and the cluster counts printed on them) the columns
 *  stand in for. The label layer stays: a symbol layer draws over
 *  extrusions, so each vehicle keeps its name while the relief is on. */
const CIRCLE_LAYERS = [LIVE_TRIPS_CLUSTER_LAYER, LIVE_TRIPS_CLUSTER_COUNT_LAYER, LIVE_TRIPS_LAYER];

/**
 * Registers the relief (delay-column) layer: one GeoJSON source and one
 * `fill-extrusion` layer, fed through `setData` on every reading.
 *
 * Declared in MapTab after `useTimelineLayers`: effects run in declaration
 * order, and playback's effect restores the live circle layers when it
 * stops, so this one has to run last to hide them again while the relief is
 * on. `playbackOn` tells the off-branch whether the circles belong to
 * playback (stay hidden) or to the live view (restore).
 */
export function useReliefLayer(
  mapRef: React.MutableRefObject<MLMap | null>,
  styleEpoch: number,
  on: boolean,
  points: ReliefPoint[],
  crossFadeMs: number,
  playbackOn: boolean,
): void {
  const theme = useThemeSignal();
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!on) {
      return whenStyleReady(map, () => {
        if (map.getLayer(RELIEF_LAYER)) map.removeLayer(RELIEF_LAYER);
        if (map.getSource(RELIEF_SOURCE)) map.removeSource(RELIEF_SOURCE);
        for (const id of CIRCLE_LAYERS) {
          if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", playbackOn ? "none" : "visible");
        }
        if (map.getLayer(TIMELINE_LAYER)) map.setLayoutProperty(TIMELINE_LAYER, "visibility", "visible");
      });
    }
    const data = reliefFeatures(points);
    return whenStyleReady(map, () => {
      for (const id of [...CIRCLE_LAYERS, TIMELINE_LAYER]) {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
      }
      const existing = map.getSource(RELIEF_SOURCE) as { setData: (d: unknown) => void } | undefined;
      if (existing) {
        existing.setData(data);
        repaintLayer(map, RELIEF_LAYER, reliefPaint(crossFadeMs));
        return;
      }
      map.addSource(RELIEF_SOURCE, { type: "geojson", data });
      const beforeId = map.getLayer(LIVE_TRIPS_LABEL_LAYER) ? LIVE_TRIPS_LABEL_LAYER : undefined;
      map.addLayer({ id: RELIEF_LAYER, type: "fill-extrusion", source: RELIEF_SOURCE, paint: reliefPaint(crossFadeMs) }, beforeId);
    });
  }, [crossFadeMs, mapRef, on, playbackOn, points, styleEpoch, theme]);
}
