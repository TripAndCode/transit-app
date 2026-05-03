import { useEffect, useRef } from "react";
import { useParams } from "react-router-dom";
import maplibregl, { Map as MLMap, Popup } from "maplibre-gl";
import { useHeatmap, useRouteShape } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import type { HeatmapProps } from "../api/types";
import { getMapStyle } from "../styles/mapStyle";
import { DELAY_RAMP } from "../styles/tokens";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { MapLegend } from "../components/MapLegend";
import { Skeleton } from "../components/Skeleton";
import { TabFilterBar } from "../components/TabFilterBar";

const SOURCE = "delays";
const LAYER = "delay-circles";
const ROUTE_SOURCE = "route-line";
const ROUTE_LAYER = "route-line-stroke";
const ROUTE_STOPS_LAYER = "route-stops";

export function MapTab() {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [ctx] = useRangeContext();
  const { data, isLoading, error, refetch } = useHeatmap(id, ctx);
  // Single-route overlay: only fetch when exactly one route is selected.
  const focusedRoute = ctx.routes.length === 1 ? ctx.routes[0] : null;
  const { data: shape } = useRouteShape(id, focusedRoute, ctx);

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

    // Route-stop click handler — registered once at init so it never
    // accumulates on filter / shape changes. The layer it targets
    // (ROUTE_STOPS_LAYER) may not exist yet; MapLibre's delegated
    // listener silently no-ops until the layer is added.
    const onRouteStopClick = (e: maplibregl.MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f) return;
      const p = f.properties as { stop_sequence: number; stop_name: string; avg_min: number; samples: number };
      popupRef.current?.remove();
      popupRef.current = new Popup({ closeButton: true, closeOnClick: true })
        .setLngLat(e.lngLat)
        .setHTML(
          `<div style="font: 13px sans-serif; min-width: 160px">
             <div style="color:#888;font-size:11px;margin-bottom:2px">停留所 #${p.stop_sequence}</div>
             <strong>${escapeHtml(p.stop_name)}</strong><br/>
             平均遅延: ${Number(p.avg_min).toFixed(1)}分<br/>
             サンプル: ${p.samples}件
           </div>`,
        )
        .addTo(m);
    };

    m.on("load", () => { styleLoadedRef.current = true; });
    m.on("click", LAYER, onClick);
    m.on("mouseenter", LAYER, onEnter);
    m.on("mouseleave", LAYER, onLeave);
    m.on("click", ROUTE_STOPS_LAYER, onRouteStopClick);
    m.on("mouseenter", ROUTE_STOPS_LAYER, onEnter);
    m.on("mouseleave", ROUTE_STOPS_LAYER, onLeave);

    mapRef.current = m;
    return () => {
      popupRef.current?.remove();
      popupRef.current = null;
      m.off("click", LAYER, onClick);
      m.off("mouseenter", LAYER, onEnter);
      m.off("mouseleave", LAYER, onLeave);
      m.off("click", ROUTE_STOPS_LAYER, onRouteStopClick);
      m.off("mouseenter", ROUTE_STOPS_LAYER, onEnter);
      m.off("mouseleave", ROUTE_STOPS_LAYER, onLeave);
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

  // Single-route overlay: thin neutral polyline + small numbered stop markers.
  // Drawn on top of the heatmap layer; cleaned up when the focus is lifted.
  // Stop-layer event handlers are registered ONCE on init (lower in the file
  // they're set up alongside the heatmap layer events) so they don't accumulate
  // on filter changes.
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;

    function clearOverlay() {
      if (!m) return;
      if (m.getLayer(ROUTE_STOPS_LAYER)) m.removeLayer(ROUTE_STOPS_LAYER);
      if (m.getLayer(ROUTE_LAYER)) m.removeLayer(ROUTE_LAYER);
      if (m.getSource(ROUTE_SOURCE)) m.removeSource(ROUTE_SOURCE);
      if (m.getSource(ROUTE_SOURCE + "-stops")) m.removeSource(ROUTE_SOURCE + "-stops");
    }

    function drawOverlay() {
      if (!m || !shape || shape.stops.length < 2) {
        clearOverlay();
        // In route mode without enough data, also keep heatmap visible.
        if (m && m.getLayer(LAYER)) m.setLayoutProperty(LAYER, "visibility", "visible");
        return;
      }
      clearOverlay();
      const coords: [number, number][] = shape.stops.map((s) => [s.lon, s.lat]);

      // Route mode: hide the heatmap (it would show isolated stops without
      // the connecting line) and use the route's stop sequence directly,
      // colored by per-stop avg delay.
      if (m.getLayer(LAYER)) m.setLayoutProperty(LAYER, "visibility", "none");

      m.addSource(ROUTE_SOURCE, {
        type: "geojson",
        data: {
          type: "Feature",
          geometry: { type: "LineString", coordinates: coords },
          properties: {},
        },
      });
      m.addLayer({
        id: ROUTE_LAYER,
        type: "line",
        source: ROUTE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#5b6cad",
          // Bolder when zoomed in so it stays visible at street-level.
          "line-width": ["interpolate", ["linear"], ["zoom"], 8, 2, 13, 4, 17, 7],
          "line-opacity": 0.7,
        },
      });

      m.addSource(ROUTE_SOURCE + "-stops", {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: shape.stops.map((s) => ({
            type: "Feature",
            geometry: { type: "Point", coordinates: [s.lon, s.lat] },
            properties: {
              stop_sequence: s.stop_sequence,
              stop_name: s.stop_name,
              avg_min: s.avg_min ?? 0,
              samples: s.samples,
            },
          })),
        },
      });
      m.addLayer({
        id: ROUTE_STOPS_LAYER,
        type: "circle",
        source: ROUTE_SOURCE + "-stops",
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            10, 4,
            14, 7,
            17, 11,
          ],
          // Color by avg_min using the same severity ramp as the heatmap.
          "circle-color": [
            "step",
            ["get", "avg_min"],
            DELAY_RAMP.ok,
            2, DELAY_RAMP.mild,
            5, DELAY_RAMP.moderate,
            10, DELAY_RAMP.severe,
          ],
          "circle-opacity": 0.95,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });

      // Fit to the route on focus.
      let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
      for (const [lon, lat] of coords) {
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
      }
      m.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 60, duration: 600 });
    }

    if (!shape) {
      // No focused route — strip overlay and bring the heatmap back.
      if (styleLoadedRef.current) {
        clearOverlay();
        if (m.getLayer(LAYER)) m.setLayoutProperty(LAYER, "visibility", "visible");
      }
      return;
    }
    if (styleLoadedRef.current) drawOverlay();
    else m.once("load", drawOverlay);
  }, [shape]);

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
      <MapLegend />
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
