import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { buildStyle, getMapStyleOverride, readMapStylePref } from "../../styles/mapStyle";
import type { RouteShapeResponse, RouteShapeStop } from "../../api/types";
import { whenStyleReady } from "../../tabs/map/styleReady";
import { createSafeMap } from "../../tabs/map/createSafeMap";
import { accentColorResolved, severeColorResolved, surfaceColorResolved } from "../../styles/tokens";
import { useThemeSignal } from "../../styles/theme";

const ROUTE_SOURCE = "analysis-route";
const ROUTE_LINE_LAYER = "analysis-line";
const SELECTED_STOP_LAYER = "analysis-stop";

export function AnalysisMap({ data, selected, height = 210, visible = true }: { data: RouteShapeResponse; selected: RouteShapeStop | undefined; height?: number; visible?: boolean }) {
  const { t, i18n } = useTranslation("design");
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const firstStyleRun = useRef(true);
  const initialLanguageRef = useRef(i18n.language);
  const [failed, setFailed] = useState(false);
  const [styleEpoch, setStyleEpoch] = useState(0);
  // MapLibre paint values are plain JS, so they cannot consume the `var()`
  // tokens the DOM recolours through. This re-runs the layer effect on a
  // theme change so the resolved hexes are read again.
  const theme = useThemeSignal();
  useEffect(() => {
    if (!container.current) return;
    const created = createSafeMap(
      { container: container.current, style: getMapStyleOverride() ?? buildStyle(readMapStylePref(), initialLanguageRef.current), center: [140.74, 40.82], zoom: 11 },
      () => setFailed(true),
    );
    if (!created.map) return created.cleanup;
    const instance = created.map;
    map.current = instance;
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    // ResizeObserver is absent in jsdom and in older embedded webviews; the
    // map still works there, it just stops tracking container resizes.
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => instance.resize());
      observer.observe(container.current);
    }
    return () => { observer?.disconnect(); instance.remove(); map.current = null; firstStyleRun.current = true; };
  }, []);
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    if (firstStyleRun.current) {
      firstStyleRun.current = false;
      return;
    }
    if (getMapStyleOverride()) return;
    instance.setStyle(buildStyle(readMapStylePref(), i18n.language), { diff: false });
    instance.once("style.load", () => setStyleEpoch((epoch) => epoch + 1));
  }, [i18n.language]);
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    return whenStyleReady(instance, () => {
      const features: GeoJSON.Feature[] = data.geometry ? [{ type: "Feature", properties: {}, geometry: data.geometry }] : [];
      if (selected && Number.isFinite(selected.lon) && Number.isFinite(selected.lat)) features.push({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [selected.lon, selected.lat] } });
      const collection: GeoJSON.FeatureCollection = { type: "FeatureCollection", features };
      const source = instance.getSource(ROUTE_SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (source) {
        source.setData(collection);
        // `theme` is a dependency of this effect so a toggle re-resolves the
        // colour tokens, but an existing source takes this branch instead of
        // re-adding the layers -- every resolved colour has to be refreshed
        // here or it stays pinned to the theme that created it.
        instance.setPaintProperty(ROUTE_LINE_LAYER, "line-color", accentColorResolved());
        instance.setPaintProperty(SELECTED_STOP_LAYER, "circle-color", severeColorResolved());
        instance.setPaintProperty(SELECTED_STOP_LAYER, "circle-stroke-color", surfaceColorResolved());
        return;
      }
      instance.addSource(ROUTE_SOURCE, { type: "geojson", data: collection });
      instance.addLayer({ id: ROUTE_LINE_LAYER, type: "line", source: ROUTE_SOURCE, filter: ["==", "$type", "LineString"], paint: { "line-color": accentColorResolved(), "line-width": 3 } });
      instance.addLayer({ id: SELECTED_STOP_LAYER, type: "circle", source: ROUTE_SOURCE, filter: ["==", "$type", "Point"], paint: { "circle-radius": 8, "circle-color": severeColorResolved(), "circle-stroke-width": 3, "circle-stroke-color": surfaceColorResolved() } });
    });
  }, [data, selected, styleEpoch, theme]);
  useEffect(() => {
    const instance = map.current;
    if (!instance || !visible) return;
    const stops = data.stops.filter((s) => Number.isFinite(s.lon) && Number.isFinite(s.lat));
    if (!stops.length) return;
    return whenStyleReady(instance, () => {
      instance.resize();
      const bounds = new maplibregl.LngLatBounds();
      stops.forEach((s) => bounds.extend([s.lon, s.lat]));
      instance.fitBounds(bounds, { padding: 30, maxZoom: 14, duration: 0 });
    });
    // Re-fit when the stop set changes (a new route/direction) or when the
    // panel becomes visible again -- while hidden (kept mounted, CSS
    // display:none), the container is zero-size and fitBounds silently
    // no-ops, so a data change picked up while off-screen must be re-applied
    // once visible rather than only reacting to `data` itself.
  }, [data, visible]);
  return <section aria-label={t("map")}><div ref={container} style={{ height: failed ? 0 : height, borderRadius: 6 }} />{failed && <p className="focus-muted">{t("mapUnavailable")}</p>}</section>;
}
