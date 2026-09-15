import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { buildStyle, getMapStyleOverride, readMapStylePref } from "../../styles/mapStyle";
import type { RouteShapeResponse, RouteShapeStop } from "../../api/types";
import { whenStyleReady } from "../../tabs/map/styleReady";
import { createSafeMap } from "../../tabs/map/createSafeMap";

export function AnalysisMap({ data, selected }: { data: RouteShapeResponse; selected: RouteShapeStop | undefined }) {
  const { t, i18n } = useTranslation("design");
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const firstStyleRun = useRef(true);
  const initialLanguageRef = useRef(i18n.language);
  const [failed, setFailed] = useState(false);
  const [styleEpoch, setStyleEpoch] = useState(0);
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
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(container.current);
    return () => { observer.disconnect(); instance.remove(); map.current = null; firstStyleRun.current = true; };
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
      const source = instance.getSource("analysis-route") as maplibregl.GeoJSONSource | undefined;
      if (source) source.setData(collection);
      else {
        instance.addSource("analysis-route", { type: "geojson", data: collection });
        instance.addLayer({ id: "analysis-line", type: "line", source: "analysis-route", filter: ["==", "$type", "LineString"], paint: { "line-color": "#268b8e", "line-width": 3 } });
        instance.addLayer({ id: "analysis-stop", type: "circle", source: "analysis-route", filter: ["==", "$type", "Point"], paint: { "circle-radius": 8, "circle-color": "#cf493d", "circle-stroke-width": 3, "circle-stroke-color": "#fff" } });
      }
    });
  }, [data, selected, styleEpoch]);
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const stops = data.stops.filter((s) => Number.isFinite(s.lon) && Number.isFinite(s.lat));
    if (!stops.length) return;
    return whenStyleReady(instance, () => {
      const bounds = new maplibregl.LngLatBounds();
      stops.forEach((s) => bounds.extend([s.lon, s.lat]));
      instance.fitBounds(bounds, { padding: 30, maxZoom: 14, duration: 0 });
    });
    // Re-fit only when the stop set itself changes (a new route/direction), not
    // when the user merely picks a different stop to inspect.
  }, [data]);
  return <section aria-label={t("map")}><div ref={container} style={{ height: failed ? 0 : 210, borderRadius: 6 }} />{failed && <p className="focus-muted">{t("mapUnavailable")}</p>}</section>;
}
