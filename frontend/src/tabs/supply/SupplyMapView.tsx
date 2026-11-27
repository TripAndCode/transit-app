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
import { DefinitionMetaBlock } from "../../components/DefinitionMetaBlock";
import { ErrorBanner } from "../../components/ErrorBanner";
import { SHARED_TABLE, td, th } from "../../components/tableStyles";
import { EM_DASH, fixedTenth, formatDateRange, formatMinutes } from "../../utils/format";
import {
  SUPPLY_DAY_TYPES,
  useNetworkSupply,
  useSupplyStop,
  type SupplyDayType,
  type SupplyHour,
  type SupplyHourWait,
  type SupplyQuery,
  type SupplyStopDetail,
} from "./supplyApi";
import {
  DEFAULT_BAND,
  DEFAULT_GAP_MIN,
  FIRST_HOUR,
  GAP_THRESHOLDS_MIN,
  LAST_HOUR,
  PER_HOUR_CLASSES,
  hourLabel,
  mapStops,
  overGapExpression,
  perHourClassExpression,
  serviceClock,
  stopBounds,
  supplyFeatures,
  supplySummary,
  type SupplyMode,
} from "./supplyModel";

const SOURCE = "supply-stops";
const LAYER = "supply-stops-circles";
// The whole prefecture-scale network at once, before the stops fit it.
const INITIAL_VIEW = { center: [132.46, 34.4] as [number, number], zoom: 10 };

// Per departures-per-hour class: none, then deepening with frequency.
const CLASS_OPACITY = [0.12, 0.3, 0.5, 0.7, 0.95];
const CLASS_RADIUS = [2.5, 3.5, 4, 4.5, 5.5];
const CLASS_LEGEND_SIZE = [5, 7, 8, 9, 11];

type CirclePaint = NonNullable<Extract<LayerSpecification, { type: "circle" }>["paint"]>;

/** Departures per hour deepen the accent by class; a gap over the threshold
 *  carries the accent and every other stop recedes, so neither mode leans on
 *  an alarm colour. The classing is in the expressions, over the features'
 *  raw figures. */
function circlePaint(mode: SupplyMode, gapMin: number, hours: number): CirclePaint {
  const accent = accentColorResolved();
  const surface = surfaceColorResolved();
  if (mode === "gap") {
    const over = overGapExpression(gapMin);
    return {
      "circle-radius": ["case", over, 5, 3],
      "circle-color": accent,
      "circle-opacity": ["case", over, 0.9, 0.18],
      "circle-stroke-width": ["case", over, 1, 0],
      "circle-stroke-color": surface,
    };
  }
  return {
    "circle-radius": perHourClassExpression(hours, CLASS_RADIUS),
    "circle-color": accent,
    "circle-opacity": perHourClassExpression(hours, CLASS_OPACITY),
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
  const [opened, setOpened] = useState<{ agencyId: number; stopId: string } | null>(null);
  const bandValid = toHour > fromHour;
  const hours = toHour - fromHour;
  const supplyQuery: SupplyQuery = { date, dayType, fromHour, toHour };
  const query = useNetworkSupply(supplyQuery);
  // An invalid band asks nothing, so the last band's answer must not stand for it.
  const data = bandValid ? query.data : undefined;
  const stops = mapStops(data);
  const summary = supplySummary(data, stops, gapMin);
  const detail = useSupplyStop(supplyQuery, bandValid ? opened : null);

  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const initialLanguage = useRef(i18n.language);
  const firstLanguageRun = useRef(true);
  const [failed, setFailed] = useState(false);
  const [styleEpoch, setStyleEpoch] = useState(0);
  const theme = useThemeSignal();
  const onStopClick = useEffectEvent((key: unknown) => {
    if (typeof key !== "string") return;
    const at = key.indexOf(":");
    setOpened({ agencyId: Number(key.slice(0, at)), stopId: key.slice(at + 1) });
  });
  const currentPaint = useEffectEvent(() => circlePaint(mode, gapMin, hours));

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
    instance.on("click", LAYER, (event) => onStopClick(event.features?.[0]?.properties?.key));
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => instance.resize());
      observer.observe(container.current);
    }
    return () => {
      observer?.disconnect();
      instance.remove();
      map.current = null;
      firstLanguageRun.current = true;
    };
  }, []);

  // A language switch rebuilds the basemap's labels, which drops the layer;
  // the bumped epoch attaches it again.
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    if (firstLanguageRun.current) {
      firstLanguageRun.current = false;
      return;
    }
    if (getMapStyleOverride()) return;
    instance.setStyle(buildStyle(readMapStylePref(), i18n.language), { diff: false });
    instance.once("style.load", () => setStyleEpoch((epoch) => epoch + 1));
  }, [i18n.language]);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const collection = supplyFeatures(mapStops(data));
    return whenStyleReady(instance, () => {
      const source = instance.getSource(SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (source) {
        source.setData(collection);
        return;
      }
      instance.addSource(SOURCE, { type: "geojson", data: collection });
      instance.addLayer({ id: LAYER, type: "circle", source: SOURCE, paint: currentPaint() });
    });
  }, [data, styleEpoch]);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const paint = circlePaint(mode, gapMin, hours);
    return whenStyleReady(instance, () => {
      if (instance.getLayer(LAYER)) repaintLayer(instance, LAYER, paint);
    });
  }, [mode, gapMin, hours, theme, styleEpoch]);

  const boundsKey = stopBounds(stops)?.join(",") ?? "";
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

  const agencies = data?.agencies ?? [];
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
      {!bandValid && (
        <p role="alert" style={MUTED}>
          {t("supply.band_invalid")}
        </p>
      )}
      {bandValid && query.error ? <ErrorBanner error={query.error} onRetry={() => void query.refetch()} /> : null}
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
      {data && detail.data && <StopDetail stop={detail.data} />}
      {data && summary.unplaced > 0 && <p style={MUTED}>{t("supply.unplaced", { count: summary.unplaced })}</p>}
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

function StopDetail({ stop }: { stop: SupplyStopDetail }) {
  const { t } = useTranslation();
  const none = t("supply.no_departure_day");
  const observedWindow = stop.observed_window;
  return (
    <div>
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
      {stop.hours.length > 0 && <HourTable hours={stop.hours} days={observedWindow?.days ?? 0} />}
      <p style={MUTED}>
        {observedWindow == null
          ? t("supply.hours.no_window")
          : t("supply.hours.window", { days: observedWindow.days, range: formatDateRange(observedWindow.first, observedWindow.last) })}
      </p>
      {observedWindow != null && (
        <>
          <p style={MUTED}>{t("supply.hours.note")}</p>
          <p style={MUTED}>{t("supply.hours.wait_note")}</p>
        </>
      )}
      {stop.definition && <DefinitionMetaBlock definition={stop.definition} />}
    </div>
  );
}

/** Each hour's planned departures beside the observed ones. The pooled
 *  counts behind a percentage are its tooltip, so a figure from a handful
 *  of departures shows how few it rests on. */
function HourTable({ hours, days }: { hours: SupplyHour[]; days: number }) {
  const { t } = useTranslation();
  return (
    <table style={SHARED_TABLE}>
      <caption style={{ ...MUTED, textAlign: "left", paddingBottom: 4 }}>{t("supply.hours.caption")}</caption>
      <thead>
        <tr>
          <th style={th()}>{t("supply.hours.col_hour")}</th>
          <th style={th({ align: "right" })}>{t("supply.hours.col_planned")}</th>
          <th style={th({ align: "right" })}>{t("supply.hours.col_observed")}</th>
          <th style={th({ align: "right" })}>{t("supply.hours.col_on_time")}</th>
          <th style={th({ align: "right" })}>{t("supply.hours.col_delay")}</th>
          <th style={th({ align: "right" })}>{t("supply.hours.col_wait")}</th>
        </tr>
      </thead>
      <tbody>
        {hours.map(({ hour, departures, observed }) => (
          <tr key={hour}>
            <td style={td()}>{hourLabel(hour)}</td>
            <td style={td({ align: "right" })}>{departures}</td>
            <td style={td({ align: "right" })}>{observed == null ? EM_DASH : fixedTenth(observed.per_day)}</td>
            <td
              style={td({ align: "right" })}
              title={
                observed == null
                  ? undefined
                  : t("supply.hours.pooled", { observed: observed.departures, days, onTime: observed.on_time })
              }
            >
              {observed?.on_time_pct == null ? (
                EM_DASH
              ) : (
                <>
                  {`${fixedTenth(observed.on_time_pct)}%`}
                  {observed.uncertain && <span className="focus-muted"> ({t("reports.wide_margin_mark")})</span>}
                </>
              )}
            </td>
            <td style={td({ align: "right" })}>{formatMinutes(observed?.avg_delay_min)}</td>
            <WaitCell wait={observed?.wait ?? null} />
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The median and 90th-percentile wait as run, the timetable's beside them
 *  in muted brackets: how the buses ran is read against what they planned. */
function WaitCell({ wait }: { wait: SupplyHourWait | null }) {
  const { t } = useTranslation();
  if (wait == null) return <td style={td({ align: "right" })}>{EM_DASH}</td>;
  const [p50, p90, scheduledP50, scheduledP90] = [
    wait.p50_min,
    wait.p90_min,
    wait.scheduled_p50_min,
    wait.scheduled_p90_min,
  ].map(fixedTenth);
  return (
    <td
      style={td({ align: "right" })}
      title={t("supply.hours.wait_title", { days: wait.days, p50, p90, scheduledP50, scheduledP90 })}
    >
      {`${p50} / ${p90}`}
      <span className="focus-muted">{` (${scheduledP50} / ${scheduledP90})`}</span>
    </td>
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
      : Array.from({ length: PER_HOUR_CLASSES }, (_, cls) => ({
          label: t(`supply.legend.class_${cls}`),
          opacity: CLASS_OPACITY[cls],
          size: CLASS_LEGEND_SIZE[cls],
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
