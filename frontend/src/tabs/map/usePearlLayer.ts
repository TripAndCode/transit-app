import { useEffect } from "react";
import type { Map as MLMap } from "maplibre-gl";
import type { LiveTripProgressResponse } from "../../api/types";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { whenStyleReady } from "./styleReady";
import { useFlowTick } from "./useFlowTick";
import { FLOW_PAINT_INTERVAL_MS, LIVE_TRIPS_LAYER, TRIP_PROGRESS_STOPS_LAYER } from "./useOperationsMapLayers";
import { PEARL_LAYER, PEARL_SOURCE, pearlGradient, pearlLine, pearlPhase } from "./pearl";

/**
 * A shimmer that travels only along the reported segments of the pinned
 * trip. It highlights where reports exist; it is not a vehicle. The chip's
 * hint says so, and the geometry enforces it: the source is the progress
 * response's located stops and nothing else. Advanced on the same 100 ms
 * tick as the route's flow dash; under reduced motion the layer stays but
 * paints the hidden gradient and no loop runs.
 */
export function usePearlLayer(
  mapRef: React.MutableRefObject<MLMap | null>,
  styleEpoch: number,
  progress: LiveTripProgressResponse | undefined,
  on: boolean,
): void {
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const line = pearlLine(progress);
  // The progress query refetches into a fresh object with the same stops;
  // keying the effect on the coordinates keeps a refetch from re-running it.
  const lineKey = line ? JSON.stringify(line.geometry.coordinates) : "";

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    return whenStyleReady(map, () => {
      if (!on || !lineKey) {
        if (map.getLayer(PEARL_LAYER)) map.removeLayer(PEARL_LAYER);
        if (map.getSource(PEARL_SOURCE)) map.removeSource(PEARL_SOURCE);
        return;
      }
      const data: GeoJSON.Feature<GeoJSON.LineString> = {
        type: "Feature",
        properties: {},
        geometry: { type: "LineString", coordinates: JSON.parse(lineKey) as GeoJSON.Position[] },
      };
      const existing = map.getSource(PEARL_SOURCE) as { setData: (d: unknown) => void } | undefined;
      if (existing) {
        existing.setData(data);
        return;
      }
      // `line-gradient` needs per-vertex progress, which only a lineMetrics
      // source computes.
      map.addSource(PEARL_SOURCE, { type: "geojson", data, lineMetrics: true });
      // On the reported trail, under its stop dots and the vehicle marks.
      const beforeId = [TRIP_PROGRESS_STOPS_LAYER, LIVE_TRIPS_LAYER].find((id) => map.getLayer(id));
      map.addLayer(
        {
          id: PEARL_LAYER,
          type: "line",
          source: PEARL_SOURCE,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-width": ["interpolate", ["linear"], ["zoom"], 11, 3, 15, 8],
            "line-gradient": pearlGradient(-1) as never,
          },
        },
        beforeId,
      );
    });
  }, [lineKey, mapRef, on, styleEpoch]);

  useFlowTick(on && line != null && !reducedMotion, FLOW_PAINT_INTERVAL_MS, (elapsed) => {
    const map = mapRef.current;
    if (!map?.getLayer(PEARL_LAYER)) return;
    map.setPaintProperty(PEARL_LAYER, "line-gradient", pearlGradient(pearlPhase(elapsed)));
  });
}
