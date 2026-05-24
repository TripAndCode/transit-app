import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import maplibregl, { Map as MLMap, Popup } from "maplibre-gl";
import { useHeatmap, useRouteShape } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import type { HeatmapProps } from "../api/types";
import { getMapStyle } from "../styles/mapStyle";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { InsightHint } from "../components/InsightHint";
import { MapLegend, type SeverityKey } from "../components/MapLegend";
import { renderStopPopupHTML } from "../components/MapPopupHTML";
import { Skeleton } from "../components/Skeleton";
import { TabFilterBar } from "../components/TabFilterBar";
import { LAYER, SOURCE, useHeatmapLayer } from "./map/useHeatmapLayer";
import { ROUTE_STOPS_LAYER, useRouteOverlay } from "./map/useRouteOverlay";

export function MapTab() {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [ctx] = useRangeContext();
  const { t } = useTranslation();
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
  // Same dance for `t` — the popup builder is invoked from delegated map
  // event handlers registered once at init, so we re-read through a ref to
  // pick up any language switch.
  const tRef = useRef(t);
  tRef.current = t;

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
        tRef.current,
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
        tRef.current,
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

  // Close any open popup before the heatmap re-renders. Without this, a
  // popup anchored to stop A would persist (showing stale numbers) after
  // a filter change drops stop A from the visible set. Matches the
  // pre-split behavior baked into the old applyData() body.
  useEffect(() => {
    popupRef.current?.remove();
    popupRef.current = null;
  }, [data, showSingleSampleStops, focusedSeverity]);

  useHeatmapLayer(mapRef, styleLoadedRef, data, showSingleSampleStops, focusedSeverity);

  useRouteOverlay(mapRef, styleLoadedRef, shape);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 400 }}>
      <TabFilterBar />
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        fontSize: 12, color: "var(--text-tertiary)",
        margin: "4px 0 8px",
      }}>
        {t("nav.map")}
        <InsightHint
          title={t("map.hint.title")}
          body={
            <>
              <strong>{t("map.hint.color_strong")}</strong>{t("map.hint.color_meaning")}<strong>{t("map.hint.size_strong")}</strong>{t("map.hint.size_meaning")}
              {t("map.hint.body_1")}
              {t("map.hint.body_2")}
              <br /><br />
              {t("map.hint.hotspots_intro")}<em>{t("map.hint.hotspots_em")}</em>{t("map.hint.hotspots_outro")}
              {t("map.hint.ontime_intro")}<em>{t("map.hint.ontime_em")}</em>{t("map.hint.ontime_outro")}
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
            title={t("map.empty.title")}
            hint={t("map.empty.hint")}
          />
        </div>
      )}
      </div>
    </div>
  );
}
