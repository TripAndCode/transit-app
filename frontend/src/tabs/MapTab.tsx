import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import maplibregl, { Map as MLMap, Popup } from "maplibre-gl";
import { useHeatmap, useRouteShape } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import type { HeatmapProps } from "../api/types";
import { getMapStyle } from "../styles/mapStyle";
import { DELAY_RAMP } from "../styles/tokens";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { InsightHint } from "../components/InsightHint";
import { MapLegend, type SeverityKey } from "../components/MapLegend";
import { renderStopPopupHTML } from "../components/MapPopupHTML";
import { Skeleton } from "../components/Skeleton";
import { TabFilterBar } from "../components/TabFilterBar";
import { LAYER, SOURCE, useHeatmapLayer } from "./map/useHeatmapLayer";

const ROUTE_SOURCE = "route-line";
const ROUTE_LAYER = "route-line-stroke";
const ROUTE_STOPS_LAYER = "route-stops";
const ROUTE_UNOBS_SOURCE = "route-unobserved";
const ROUTE_UNOBS_LAYER = "route-unobserved-stops";

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
  const popupRef = useRef<Popup | null>(null);
  const styleLoadedRef = useRef(false);
  const [showSingleSampleStops, setShowSingleSampleStops] = useState(false);
  const [focusedSeverity, setFocusedSeverity] = useState<SeverityKey | null>(null);

  // ctx changes on filter/range edits; click handlers are registered once at
  // init, so we read through this ref to always see the current period.
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;

  // Init the map once. Click / hover handlers are registered here too —
  // MapLibre's delegated listeners no-op until the named layer exists,
  // so handlers can safely target layers that are added later by the
  // data effects below.
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
      const c = ctxRef.current;
      const html = renderStopPopupHTML(
        {
          stop_name: p.stop_name,
          stop_code: p.stop_code,
          platform_code: p.platform_code,
          stop_id: p.stop_id,
          avg_min: Number(p.avg_delay_min),
          samples: p.samples,
          contributing_routes: (p.route_codes || "").split(",").filter(Boolean),
        },
        { from: c.from, to: c.to },
      );
      popupRef.current?.remove();
      popupRef.current = new Popup({ closeButton: true, closeOnClick: true })
        .setLngLat(e.lngLat)
        .setHTML(html)
        .addTo(m);
      // Focus the clicked stop: gentle camera move, zoom only if currently zoomed out.
      const targetZoom = Math.max(m.getZoom(), 14);
      m.easeTo({ center: e.lngLat, zoom: targetZoom, duration: 600 });
    };
    let hoveredId: number | string | undefined;
    const onEnter = (e: maplibregl.MapLayerMouseEvent) => {
      m.getCanvas().style.cursor = "pointer";
      const f = e.features?.[0];
      if (f && f.id !== undefined && f.source === SOURCE) {
        if (hoveredId !== undefined) {
          m.setFeatureState({ source: SOURCE, id: hoveredId }, { hover: false });
        }
        hoveredId = f.id;
        m.setFeatureState({ source: SOURCE, id: hoveredId }, { hover: true });
      }
    };
    const onLeave = () => {
      m.getCanvas().style.cursor = "";
      if (hoveredId !== undefined) {
        m.setFeatureState({ source: SOURCE, id: hoveredId }, { hover: false });
        hoveredId = undefined;
      }
    };

    // Route-stop click handler — registered once at init so it never
    // accumulates on filter / shape changes. The layer it targets
    // (ROUTE_STOPS_LAYER) may not exist yet; MapLibre's delegated
    // listener silently no-ops until the layer is added.
    const onRouteStopClick = (e: maplibregl.MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f) return;
      const p = f.properties as {
        stop_sequence: number;
        stop_name: string;
        stop_id?: string | null;
        stop_code?: string | null;
        platform_code?: string | null;
        avg_min: number;
        samples: number;
      };
      const c = ctxRef.current;
      const html = renderStopPopupHTML(
        {
          stop_name: p.stop_name,
          stop_code: p.stop_code,
          platform_code: p.platform_code,
          stop_id: p.stop_id,
          stop_sequence: p.stop_sequence,
          avg_min: Number(p.avg_min),
          samples: p.samples,
          active_route: c.routes[0] ?? null,
        },
        { from: c.from, to: c.to },
      );
      popupRef.current?.remove();
      popupRef.current = new Popup({ closeButton: true, closeOnClick: true })
        .setLngLat(e.lngLat)
        .setHTML(html)
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

  useHeatmapLayer(mapRef, styleLoadedRef, data, showSingleSampleStops, focusedSeverity);

  // Single-route overlay: thin neutral polyline + small numbered stop markers.
  // Drawn on top of the heatmap layer; cleaned up when the focus is lifted.
  // (Click handlers for both layers are registered once in the init effect
  // above so they don't accumulate on filter changes.)
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;

    function clearOverlay() {
      if (!m) return;
      if (m.getLayer(ROUTE_UNOBS_LAYER)) m.removeLayer(ROUTE_UNOBS_LAYER);
      if (m.getLayer(ROUTE_STOPS_LAYER)) m.removeLayer(ROUTE_STOPS_LAYER);
      if (m.getLayer(ROUTE_LAYER)) m.removeLayer(ROUTE_LAYER);
      if (m.getSource(ROUTE_SOURCE)) m.removeSource(ROUTE_SOURCE);
      if (m.getSource(ROUTE_SOURCE + "-stops")) m.removeSource(ROUTE_SOURCE + "-stops");
      if (m.getSource(ROUTE_UNOBS_SOURCE)) m.removeSource(ROUTE_UNOBS_SOURCE);
    }

    function drawOverlay() {
      if (!m || !shape || shape.stops.length < 2) {
        clearOverlay();
        // In route mode without enough data, also keep heatmap visible.
        if (m && m.getLayer(LAYER)) m.setLayoutProperty(LAYER, "visibility", "visible");
        return;
      }
      clearOverlay();
      const geomCoords = shape.geometry?.coordinates;
      const coords: [number, number][] =
        geomCoords && geomCoords.length >= 2
          ? (geomCoords as [number, number][])
          : shape.stops.map((s) => [s.lon, s.lat]);

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
              stop_id: s.stop_id ?? null,
              stop_code: s.stop_code ?? null,
              platform_code: s.platform_code ?? null,
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

      // Unobserved stops on the chosen shape — hollow grey rings so the
      // route topology is visible even where no delay data has accrued.
      const unobserved = shape.unobserved_stops ?? [];
      if (unobserved.length > 0) {
        m.addSource(ROUTE_UNOBS_SOURCE, {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: unobserved.map((s) => ({
              type: "Feature",
              geometry: { type: "Point", coordinates: [s.lon, s.lat] },
              properties: {
                stop_sequence: s.stop_sequence,
                stop_name: s.stop_name,
                stop_id: s.stop_id ?? null,
                stop_code: s.stop_code ?? null,
                platform_code: s.platform_code ?? null,
              },
            })),
          },
        });
        m.addLayer({
          id: ROUTE_UNOBS_LAYER,
          type: "circle",
          source: ROUTE_UNOBS_SOURCE,
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2.5, 14, 4, 17, 6],
            "circle-color": "rgba(255,255,255,0.0)",
            "circle-stroke-width": 1,
            "circle-stroke-color": "rgba(0,0,0,0.35)",
          },
        });
      }

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

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 400 }}>
      <TabFilterBar />
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        fontSize: 12, color: "var(--text-tertiary)",
        margin: "4px 0 8px",
      }}>
        地図
        <InsightHint
          title="地図の読み方"
          body={
            <>
              <strong>色</strong>は平均遅延の段階、<strong>大きさ</strong>はサンプル数（観測の多さ）。
              凡例の色をクリックすると該当の遅延帯のみ表示。停留所をクリックで詳細と拡大。
              経路を 1 つに絞ると、実際の道路形状で系統が描画されます。
              <br /><br />
              ホットスポット（赤・大きい円）は<em>慢性的に遅れる</em>停留所。
              小さい緑の円は<em>定刻運行</em>。期間・曜日・時間帯フィルタで「いつ」遅れるかを掘り下げられます。
            </>
          }
        />
      </div>
      {/* Render the error inline above the map instead of replacing the
          whole tab — losing the filter bar and tab nav on a transient
          5xx is jarring. The map container below stays mounted so the
          user keeps their context (zoom / pan / open popup) and just
          sees a calm "再試行" pill. */}
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
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
      <MapLegend
        showSingleSampleStops={showSingleSampleStops}
        onShowSingleSampleStopsChange={setShowSingleSampleStops}
        focusedSeverity={focusedSeverity}
        onFocusedSeverityChange={setFocusedSeverity}
      />
      {/* Empty state covers the map only when there's nothing to show.
          In single-route mode the route overlay (line + numbered stops)
          is the primary visual and may be present even if the heatmap
          aggregation returned 0 stops with geom — so we suppress the
          empty banner whenever the route overlay has data. */}
      {data && data.features.length === 0 && !(shape && shape.stops.length >= 2) && (
        <div style={{ position: "absolute", inset: 0, background: "var(--bg-page)" }}>
          <EmptyState
            title="まだ表示できるデータがありません"
            hint="初回データ取得中の可能性があります。数分後に自動で表示されます。"
          />
        </div>
      )}
      </div>
    </div>
  );
}
