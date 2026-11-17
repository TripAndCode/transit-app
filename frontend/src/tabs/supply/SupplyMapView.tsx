import { useEffect, useEffectEvent, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { LayerSpecification } from "maplibre-gl";
import { buildStyle, getMapStyleOverride, readMapStylePref } from "../../styles/mapStyle";
import { accentColorResolved, surfaceColorResolved } from "../../styles/tokens";
import { useThemeSignal } from "../../styles/theme";
import { createSafeMap } from "../map/createSafeMap";
import { whenStyleReady } from "../map/styleReady";
import { repaintLayer } from "../map/repaintLayer";
import { ErrorBanner } from "../../components/ErrorBanner";
import { SUPPLY_DAY_TYPES, useNetworkSupply, type SupplyDayType, type SupplyStop } from "./supplyApi";
import {
  DEFAULT_BAND,
  DEFAULT_GAP_MIN,
  FIRST_HOUR,
  GAP_THRESHOLDS_MIN,
  LAST_HOUR,
  serviceClock,
  hourLabel,
  stopBounds,
  stopKey,
  supplyFeatures,
  supplySummary,
  type SupplyMode,
} from "./supplyModel";

const SOURCE = "supply-stops";
const LAYER = "supply-stops-circles";
// The whole prefecture-scale network at once, before the stops fit it.
const INITIAL_VIEW = { center: [132.46, 34.4] as [number, number], zoom: 10 };

type CirclePaint = NonNullable<Extract<LayerSpecification, { type: "circle" }>["paint"]>;

/** Departures per hour deepen the accent by class; a gap over the threshold
 *  carries the accent and every other stop recedes, so neither mode leans on
 *  an alarm colour. */
function circlePaint(mode: SupplyMode): CirclePaint {
  const accent = accentColorResolved();
  const surface = surfaceColorResolved();
  if (mode === "gap") {
    return {
      "circle-radius": ["case", ["get", "over_gap"], 5, 3],
      "circle-color": accent,
      "circle-opacity": ["case", ["get", "over_gap"], 0.9, 0.18],
      "circle-stroke-width": ["case", ["get", "over_gap"], 1, 0],
      "circle-stroke-color": surface,
    };
  }
  return {
    "circle-radius": ["step", ["get", "per_hour_class"], 2.5, 1, 3.5, 2, 4, 3, 4.5, 4, 5.5],
    "circle-color": accent,
    "circle-opacity": ["step", ["get", "per_hour_class"], 0.12, 1, 0.3, 2, 0.5, 3, 0.7, 4, 0.95],
    "circle-stroke-width": 1,
    "circle-stroke-color": surface,
  };
}

const HOURS = Array.from({ length: LAST_HOUR - FIRST_HOUR + 1 }, (_, i) => FIRST_HOUR + i);

const CONTROL_ROW: CSSProperties = { display: "flex", flexWrap: "wrap", gap: 12, alignItems: "end", marginBottom: 10 };
const FIELD: CSSProperties = { display: "flex", flexDirection: "column", gap: 2, fontSize: "var(--text-sm)" };
const MUTED: CSSProperties = { color: "var(--text-secondary)", fontSize: "var(--text-sm)" };

/** Every operator's stops on one map by planned departures over a band of
 *  hours, or by their longest gap against a threshold. */
export function SupplyMapView() {
  const { t, i18n } = useTranslation();
  const [date, setDate] = useState("");
  const [dayType, setDayType] = useState<SupplyDayType>("weekday");
  const [fromHour, setFromHour] = useState<number>(DEFAULT_BAND.fromHour);
  const [toHour, setToHour] = useState<number>(DEFAULT_BAND.toHour);
  const [mode, setMode] = useState<SupplyMode>("departures");
  const [gapMin, setGapMin] = useState<number>(DEFAULT_GAP_MIN);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const bandValid = toHour > fromHour;
  const query = useNetworkSupply({ date, dayType, fromHour, toHour });
  const data = query.data;
  const stops = data?.stops ?? [];
  const agencies = data?.agencies ?? [];
  const summary = supplySummary(agencies, stops, gapMin);
  const selected = stops.find((s) => stopKey(s) === selectedKey);

  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const initialLanguage = useRef(i18n.language);
  const [failed, setFailed] = useState(false);
  const theme = useThemeSignal();
  const onStopClick = useEffectEvent((key: string | null) => setSelectedKey(key));

  useEffect(() => {
    if (!container.current) return;
    const created = createSafeMap(
      {
        container: container.current,
        style: getMapStyleOverride() ?? buildStyle(readMapStylePref(), initialLanguage.current),
        ...INITIAL_VIEW,
      },
      () => setFailed(true),
    );
    if (!created.map) return created.cleanup;
    const instance = created.map;
    map.current = instance;
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    instance.on("click", LAYER, (event) => {
      const key = event.features?.[0]?.properties?.key;
      onStopClick(typeof key === "string" ? key : null);
    });
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => instance.resize());
      observer.observe(container.current);
    }
    return () => {
      observer?.disconnect();
      instance.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const collection = supplyFeatures(data?.stops ?? [], gapMin);
    return whenStyleReady(instance, () => {
      const paint = circlePaint(mode);
      const source = instance.getSource(SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (source) {
        source.setData(collection);
        repaintLayer(instance, LAYER, paint);
        return;
      }
      instance.addSource(SOURCE, { type: "geojson", data: collection });
      instance.addLayer({ id: LAYER, type: "circle", source: SOURCE, paint });
    });
  }, [data, gapMin, mode, theme]);

  const bounds = stopBounds(stops);
  const boundsKey = bounds?.join(",") ?? "";
  useEffect(() => {
    const instance = map.current;
    if (!instance || !boundsKey) return;
    const [west, south, east, north] = boundsKey.split(",").map(Number);
    return whenStyleReady(instance, () => {
      instance.fitBounds(
        [
          [west, south],
          [east, north],
        ],
        { padding: 30, maxZoom: 14, duration: 0 },
      );
    });
  }, [boundsKey]);

  const notShown = agencies.filter((a) => a.status !== "ok");
  const read = agencies.filter((a) => a.status === "ok");
  return (
    <section aria-label={t("supply.map_label")} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={CONTROL_ROW}>
        <label style={FIELD}>
          {t("supply.date_label")}
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-describedby="supply-date-hint" />
          <span id="supply-date-hint" style={MUTED}>
            {t("supply.date_hint")}
          </span>
        </label>
        <label style={FIELD}>
          {t("supply.day_type_label")}
          <select value={dayType} onChange={(e) => setDayType(e.target.value as SupplyDayType)}>
            {SUPPLY_DAY_TYPES.map((d) => (
              <option key={d} value={d}>
                {t(`supply.day_type.${d}`)}
              </option>
            ))}
          </select>
        </label>
        <label style={FIELD}>
          {t("supply.band_from")}
          <select value={fromHour} onChange={(e) => setFromHour(Number(e.target.value))}>
            {HOURS.slice(0, -1).map((h) => (
              <option key={h} value={h}>
                {hourLabel(h)}
              </option>
            ))}
          </select>
        </label>
        <label style={FIELD}>
          {t("supply.band_to")}
          <select value={toHour} onChange={(e) => setToHour(Number(e.target.value))}>
            {HOURS.slice(1).map((h) => (
              <option key={h} value={h}>
                {hourLabel(h)}
              </option>
            ))}
          </select>
        </label>
        <label style={FIELD}>
          {t("supply.mode_label")}
          <select value={mode} onChange={(e) => setMode(e.target.value as SupplyMode)}>
            <option value="departures">{t("supply.mode.departures")}</option>
            <option value="gap">{t("supply.mode.gap")}</option>
          </select>
        </label>
        <label style={FIELD}>
          {t("supply.gap_threshold")}
          <select value={gapMin} onChange={(e) => setGapMin(Number(e.target.value))}>
            {GAP_THRESHOLDS_MIN.map((m) => (
              <option key={m} value={m}>
                {t("supply.minutes", { count: m })}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!bandValid && <p role="alert" style={MUTED}>{t("supply.band_invalid")}</p>}
      {query.error ? <ErrorBanner error={query.error} onRetry={() => void query.refetch()} /> : null}
      <Legend mode={mode} gapMin={gapMin} />
      <div ref={container} style={{ height: failed ? 0 : 480, borderRadius: 6 }} />
      {failed && <p style={MUTED}>{t("supply.map_unavailable")}</p>}
      {data && (
        <p style={{ fontSize: "var(--text-sm)" }}>
          {summary.stops === 0
            ? t("supply.empty")
            : t("supply.summary", {
                stops: summary.stops,
                agencies: summary.readAgencies,
                over: summary.overGap,
                minutes: gapMin,
              })}
        </p>
      )}
      {selected && <StopDetail stop={selected} />}
      {summary.unplaced > 0 && <p style={MUTED}>{t("supply.unplaced", { count: summary.unplaced })}</p>}
      {notShown.length > 0 && (
        <div style={MUTED}>
          <div>{t("supply.not_shown")}</div>
          <ul style={{ margin: "2px 0 0", paddingInlineStart: 18 }}>
            {notShown.map((a) => (
              <li key={a.agency_id}>{t(`supply.status.${a.status}`, { name: a.agency_name })}</li>
            ))}
          </ul>
        </div>
      )}
      {read.length > 0 && (
        <details style={MUTED}>
          <summary>{t("supply.timetables_read")}</summary>
          <ul style={{ margin: "2px 0 0", paddingInlineStart: 18 }}>
            {read.map((a) => (
              <li key={a.agency_id}>
                {t("supply.typical_day", { name: a.agency_name, version: a.static_version_id, day: a.typical_day })}
              </li>
            ))}
          </ul>
        </details>
      )}
      <p style={MUTED}>{t("supply.note")}</p>
    </section>
  );
}

function StopDetail({ stop }: { stop: SupplyStop }) {
  const { t } = useTranslation();
  const none = t("supply.no_departure_day");
  return (
    <p style={{ fontSize: "var(--text-sm)" }}>
      {t("supply.stop_detail", {
        name: stop.stop_name ?? stop.stop_id,
        departures: stop.departures,
        perHour: stop.per_hour,
        gap: stop.longest_gap_min,
        first: stop.first_sec == null ? none : serviceClock(stop.first_sec),
        last: stop.last_sec == null ? none : serviceClock(stop.last_sec),
      })}
    </p>
  );
}

function Legend({ mode, gapMin }: { mode: SupplyMode; gapMin: number }) {
  const { t } = useTranslation();
  const items =
    mode === "gap"
      ? [
          { label: t("supply.legend.over_gap", { minutes: gapMin }), opacity: 0.9, size: 10 },
          { label: t("supply.legend.within_gap", { minutes: gapMin }), opacity: 0.18, size: 6 },
        ]
      : [0, 1, 2, 3, 4].map((cls) => ({
          label: t(`supply.legend.class_${cls}`),
          opacity: [0.12, 0.3, 0.5, 0.7, 0.95][cls],
          size: [5, 7, 8, 9, 11][cls],
        }));
  return (
    <ul style={{ display: "flex", flexWrap: "wrap", gap: 12, listStyle: "none", margin: 0, padding: 0, ...MUTED }}>
      {items.map((item) => (
        <li key={item.label} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <span
            aria-hidden="true"
            style={{
              width: item.size,
              height: item.size,
              borderRadius: "50%",
              background: "var(--accent)",
              opacity: item.opacity,
            }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}
