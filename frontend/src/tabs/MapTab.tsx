import { useEffect, useRef } from "react";
import { useParams } from "react-router-dom";
import maplibregl, { Map as MLMap, Popup } from "maplibre-gl";
import { useHeatmap } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import type { HeatmapProps } from "../api/types";
import { getMapStyle } from "../styles/mapStyle";
import { DELAY_RAMP } from "../styles/tokens";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { Skeleton } from "../components/Skeleton";
import { TabFilterBar } from "../components/TabFilterBar";

const SOURCE = "delays";
const LAYER = "delay-circles";

export function MapTab() {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [ctx] = useRangeContext();
  const { data, isLoading, error, refetch } = useHeatmap(id, ctx);

  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const fittedRef = useRef(false);
  const popupRef = useRef<Popup | null>(null);
  const styleLoadedRef = useRef(false);

  // init map once; register layer handlers once after style load
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const m = new maplibregl.Map({
      container: containerRef.current,
      style: getMapStyle(),
      center: [140.7474, 40.8246], // Aomori default
      zoom: 11,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

    const onClick = (e: maplibregl.MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f) return;
      const p = f.properties as HeatmapProps;
      popupRef.current?.remove();
      popupRef.current = new Popup({ closeButton: true, closeOnClick: true })
        .setLngLat(e.lngLat)
        .setHTML(
          `<div style="font: 13px sans-serif">
             <strong>${escapeHtml(p.stop_name)}</strong><br/>
             平均遅延: ${Number(p.avg_delay_min).toFixed(1)}分<br/>
             サンプル: ${p.samples}件
           </div>`,
        )
        .addTo(m);
    };
    const onEnter = () => { m.getCanvas().style.cursor = "pointer"; };
    const onLeave = () => { m.getCanvas().style.cursor = ""; };

    m.on("load", () => { styleLoadedRef.current = true; });
    m.on("click", LAYER, onClick);
    m.on("mouseenter", LAYER, onEnter);
    m.on("mouseleave", LAYER, onLeave);

    mapRef.current = m;
    return () => {
      popupRef.current?.remove();
      popupRef.current = null;
      m.off("click", LAYER, onClick);
      m.off("mouseenter", LAYER, onEnter);
      m.off("mouseleave", LAYER, onLeave);
      m.remove();
      mapRef.current = null;
      styleLoadedRef.current = false;
    };
  }, []);

  // sync data into source/layer
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !data) return;
    const snapshot = data;

    function applyData() {
      if (!m) return;
      popupRef.current?.remove();
      popupRef.current = null;

      if (m.getLayer(LAYER)) m.removeLayer(LAYER);
      if (m.getSource(SOURCE)) m.removeSource(SOURCE);

      m.addSource(SOURCE, { type: "geojson", data: snapshot });
      m.addLayer({
        id: LAYER,
        type: "circle",
        source: SOURCE,
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["get", "samples"],
            1, 3,
            500, 12,
          ],
          "circle-color": [
            "step",
            ["get", "avg_delay_min"],
            DELAY_RAMP.ok,
            2, DELAY_RAMP.mild,
            5, DELAY_RAMP.moderate,
            10, DELAY_RAMP.severe,
          ],
          "circle-opacity": [
            "interpolate",
            ["linear"],
            ["get", "samples"],
            1, 0.35,
            50, 0.7,
            500, 0.85,
          ],
          "circle-stroke-width": 0.5,
          "circle-stroke-color": "#fff",
        },
      });

      // Fit bounds only on the first data load — subsequent filter changes
      // keep the user's current pan/zoom so the camera doesn't fight them.
      if (!fittedRef.current) {
        if (snapshot.features.length === 1) {
          const [lon, lat] = snapshot.features[0].geometry.coordinates;
          m.flyTo({ center: [lon, lat], zoom: 13, duration: 600 });
          fittedRef.current = true;
        } else if (snapshot.features.length > 1) {
          let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
          for (const f of snapshot.features) {
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
  }, [data]);

  if (error) return <ErrorBanner error={error} onRetry={() => refetch()} />;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 400 }}>
      <TabFilterBar />
      <div style={{ position: "relative", flex: 1, minHeight: 400 }}>
      {isLoading && (
        <div style={{ position: "absolute", inset: 0, padding: 24, zIndex: 1 }}>
          <Skeleton height="100%" />
        </div>
      )}
      <div
        ref={containerRef}
        style={{ position: "absolute", inset: 0, borderRadius: "var(--radius-lg)", overflow: "hidden" }}
      />
      {data && data.features.length === 0 && (
        <div style={{ position: "absolute", inset: 0, background: "var(--bg-page)" }}>
          <EmptyState
            title="ヒートマップデータがありません"
            hint="集計を実行してください"
            hintMono="make analyze"
          />
        </div>
      )}
      </div>
    </div>
  );
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!),
  );
}
