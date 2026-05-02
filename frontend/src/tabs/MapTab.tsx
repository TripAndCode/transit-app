import { useEffect, useRef } from "react";
import { useParams } from "react-router-dom";
import maplibregl, { Map as MLMap, Popup } from "maplibre-gl";
import { useHeatmap } from "../api/hooks";
import { getMapStyle } from "../styles/mapStyle";
import { DELAY_RAMP } from "../styles/tokens";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { Skeleton } from "../components/Skeleton";

const SOURCE = "delays";
const LAYER = "delay-circles";

export function MapTab() {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const { data, isLoading, error, refetch } = useHeatmap(id);

  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);

  // init map once
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const m = new maplibregl.Map({
      container: containerRef.current,
      style: getMapStyle(),
      center: [140.7474, 40.8246], // Aomori default
      zoom: 11,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = m;
    return () => {
      m.remove();
      mapRef.current = null;
    };
  }, []);

  // sync data
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !data) return;
    const snapshot = data;

    function updateData() {
      if (!m) return;
      if (m.getLayer(LAYER)) m.removeLayer(LAYER);
      if (m.getSource(SOURCE)) m.removeSource(SOURCE);
      m.addSource(SOURCE, {
        type: "geojson",
        data: snapshot as GeoJSON.FeatureCollection<GeoJSON.Point>,
      });
      m.addLayer({
        id: LAYER,
        type: "circle",
        source: SOURCE,
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["get", "samples"],
            1, 4,
            500, 20,
          ],
          "circle-color": [
            "step",
            ["get", "avg_delay_min"],
            DELAY_RAMP.ok,
            2, DELAY_RAMP.mild,
            5, DELAY_RAMP.moderate,
            10, DELAY_RAMP.severe,
          ],
          "circle-opacity": 0.75,
          "circle-stroke-width": 1,
          "circle-stroke-color": "#fff",
        },
      });
      // popup on click
      m.on("click", LAYER, (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties as { stop_name: string; avg_delay_min: number; samples: number };
        new Popup({ closeButton: true, closeOnClick: true })
          .setLngLat(e.lngLat)
          .setHTML(
            `<div style="font: 13px sans-serif">
               <strong>${escapeHtml(p.stop_name)}</strong><br/>
               平均遅延: ${Number(p.avg_delay_min).toFixed(1)}分<br/>
               サンプル: ${p.samples}件
             </div>`
          )
          .addTo(m);
      });
      m.on("mouseenter", LAYER, () => { m.getCanvas().style.cursor = "pointer"; });
      m.on("mouseleave", LAYER, () => { m.getCanvas().style.cursor = ""; });

      // fit bounds if features present
      if (snapshot.features.length > 0) {
        const lons = snapshot.features.map((f) => f.geometry.coordinates[0]);
        const lats = snapshot.features.map((f) => f.geometry.coordinates[1]);
        const bounds: [[number, number], [number, number]] = [
          [Math.min(...lons), Math.min(...lats)],
          [Math.max(...lons), Math.max(...lats)],
        ];
        m.fitBounds(bounds, { padding: 40, duration: 600 });
      }
    }

    if (m.isStyleLoaded()) updateData();
    else m.once("load", updateData);
  }, [data]);

  if (error) return <ErrorBanner error={error} onRetry={() => refetch()} />;

  return (
    <div style={{ position: "relative", height: "100%", minHeight: 400 }}>
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
  );
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!),
  );
}
