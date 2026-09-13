import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { buildStyle, getMapStyleOverride, readMapStylePref } from "../../styles/mapStyle";
import type { RouteShapeResponse, RouteShapeStop } from "../../api/types";
import { whenStyleReady } from "../../tabs/map/styleReady";

export function AnalysisMap({ data, selected }: { data: RouteShapeResponse; selected: RouteShapeStop | undefined }) {
  const { t, i18n } = useTranslation("design");
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!container.current) return;
    let instance: maplibregl.Map;
    try {
      instance = new maplibregl.Map({ container: container.current, style: getMapStyleOverride() ?? buildStyle(readMapStylePref(), i18n.language), center: [140.74, 40.82], zoom: 11 });
    } catch {
      const frame = requestAnimationFrame(() => setFailed(true));
      return () => cancelAnimationFrame(frame);
    }
    map.current = instance;
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(container.current);
    return () => { observer.disconnect(); instance.remove(); map.current = null; };
  }, [i18n.language]);
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    return whenStyleReady(instance, () => {
      const features: GeoJSON.Feature[] = data.geometry ? [{ type: "Feature", properties: {}, geometry: data.geometry }] : [];
      if (selected && Number.isFinite(selected.lon) && Number.isFinite(selected.lat)) features.push({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [selected.lon, selected.lat] } });
      const collection: GeoJSON.FeatureCollection = { type: "FeatureCollection", features };
      const source = instance.getSource("analysis-route") as maplibregl.GeoJSONSource | undefined;
      if (source) source.setData(collection);
      else {
        instance.addSource("analysis-route", { type: "geojson", data: collection });
        instance.addLayer({ id: "analysis-line", type: "line", source: "analysis-route", filter: ["==", "$type", "LineString"], paint: { "line-color": "#268b8e", "line-width": 3 } });
        instance.addLayer({ id: "analysis-stop", type: "circle", source: "analysis-route", filter: ["==", "$type", "Point"], paint: { "circle-radius": 8, "circle-color": "#cf493d", "circle-stroke-width": 3, "circle-stroke-color": "#fff" } });
      }
      const stops = data.stops.filter((s) => Number.isFinite(s.lon) && Number.isFinite(s.lat));
      if (stops.length) {
        const bounds = new maplibregl.LngLatBounds();
        stops.forEach((s) => bounds.extend([s.lon, s.lat]));
        instance.fitBounds(bounds, { padding: 30, maxZoom: 14, duration: 0 });
      }
    });
  }, [data, selected, i18n.language]);
  return <section aria-label={t("map")}><div ref={container} style={{ height: failed ? 0 : 210, borderRadius: 6 }} />{failed && <p className="focus-muted">{t("mapUnavailable")}</p>}</section>;
}
