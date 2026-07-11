import { useEffect, useRef } from "react";
import maplibregl, { type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useTranslation } from "react-i18next";
import { useHeatmap } from "../api/hooks";
import type { RangeCtx } from "../api/rangeContext";
import { buildStyle, getMapStyleOverride, readMapStylePref } from "../styles/mapStyle";
import { useHeatmapLayer } from "../tabs/map/useHeatmapLayer";

type Props = {
  agencyId: number;
  ctx: RangeCtx;
};

/** Decorative, non-interactive live snapshot of the agency's stops, colored
 *  by the same delay-severity ramp as the full Map tab — reuses
 *  useHeatmapLayer (MapTab's own heatmap-rendering hook) rather than
 *  duplicating any map logic. No click handlers, no legend, no zoom/pan:
 *  this is a glance-strip, not a second full map. Always loaded via
 *  React.lazy (see OverviewTab.tsx) so maplibre-gl stays out of Overview's
 *  default bundle chunk. */
export function OverviewMiniMap({ agencyId, ctx }: Props) {
  const { i18n } = useTranslation();
  const { data } = useHeatmap(agencyId, ctx);
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const m = new maplibregl.Map({
      container: containerRef.current,
      style: getMapStyleOverride() ?? buildStyle(readMapStylePref(), i18n.language),
      center: [140.7474, 40.8246],
      zoom: 11,
      interactive: false,
      attributionControl: false,
    });
    mapRef.current = m;
    return () => {
      m.remove();
      mapRef.current = null;
    };
    // Intentionally init once — a language change re-rendering the basemap's
    // labels isn't worth tearing down and rebuilding this decorative strip.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // showSingleSampleStops=false, focusedSeverity=null, styleEpoch=0 (fixed —
  // there's no basemap-style switcher on this strip to bump it), matching
  // MapTab's own defaults for its equivalent stop-dot layer.
  useHeatmapLayer(mapRef, data, false, null, agencyId, 0);

  return (
    <div className="ov-map-strip">
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      <div className="ov-map-fade-t" />
      <div className="ov-map-fade-b" />
    </div>
  );
}
