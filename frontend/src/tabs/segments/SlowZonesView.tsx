import { useEffect, useEffectEvent, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { LayerSpecification } from "maplibre-gl";
import { apiUrl } from "../../api/client";
import { useAgencyId } from "../../api/useAgencyId";
import { useCapabilityTier } from "../../api/useCapabilityTier";
import { useIsAdmin } from "../../api/useIsAdmin";
import { CsvLink } from "../../components/analysis/CsvLink";
import { segmentRuntimeNotShown } from "../../components/analysis/notShown";
import { EmptyState } from "../../components/EmptyState";
import { ErrorBanner } from "../../components/ErrorBanner";
import { InlineSparkline } from "../../components/InlineSparkline";
import { SHARED_TABLE, td, th } from "../../components/tableStyles";
import { buildStyle, getMapStyleOverride, readMapStylePref } from "../../styles/mapStyle";
import { accentColorResolved } from "../../styles/tokens";
import { useThemeSignal } from "../../styles/theme";
import { EM_DASH, formatDate, formatNumber } from "../../utils/format";
import { createSafeMap } from "../map/createSafeMap";
import { repaintLayer } from "../map/repaintLayer";
import { whenStyleReady } from "../map/styleReady";
import type { Band } from "../../api/timeBands";
import { buildTimeBandOptions } from "../../components/timeBandOptions";
import {
  SLOW_ZONE_DAY_TYPES,
  useSlowZones,
  type SegmentWeek,
  type SlowZoneDayType,
  type SlowZoneSegmentData,
} from "./slowZonesApi";
import {
  EXCESS_CLASS_EDGES_SEC,
  observedExcess,
  segmentBounds,
  segmentFeatures,
  segmentKey,
  slowZoneSegments,
  streakFillsWindow,
} from "./slowZonesModel";

const SOURCE = "slow-segments";
const LAYER = "slow-segments-lines";
const INITIAL_VIEW = { center: [132.46, 34.4] as [number, number], zoom: 10 };
// Opacity per excess class, the first for a segment observed too little.
const CLASS_OPACITY = [0.12, 0.22, 0.4, 0.6, 0.8, 0.95] as const;

type LinePaint = NonNullable<Extract<LayerSpecification, { type: "line" }>["paint"]>;

/** One colour deepened by the newest week's excess; a slow zone is drawn
 *  wider rather than in another hue, so nothing reads as an alarm. */
function linePaint(): LinePaint {
  return {
    "line-color": accentColorResolved(),
    "line-width": ["case", ["get", "slow"], 5, 2.5],
    "line-opacity": [
      "step",
      ["get", "excess_class"],
      CLASS_OPACITY[0],
      0,
      CLASS_OPACITY[1],
      1,
      CLASS_OPACITY[2],
      2,
      CLASS_OPACITY[3],
      3,
      CLASS_OPACITY[4],
      4,
      CLASS_OPACITY[5],
    ],
  };
}

const CONTROL_ROW: CSSProperties = { display: "flex", flexWrap: "wrap", gap: 12, alignItems: "end", marginBottom: 10 };
const FIELD: CSSProperties = { display: "flex", flexDirection: "column", gap: 2, fontSize: "var(--text-sm)" };
const MUTED: CSSProperties = { color: "var(--text-secondary)", fontSize: "var(--text-sm)" };
const LINK_BUTTON: CSSProperties = {
  padding: 0,
  border: "none",
  background: "none",
  color: "var(--accent-strong)",
  font: "inherit",
  textAlign: "left",
  cursor: "pointer",
};

function seconds(value: number | null | undefined): string {
  return value == null ? EM_DASH : formatNumber(Math.round(value));
}

function oneDecimal(value: number | null | undefined): string {
  return value == null ? EM_DASH : formatNumber(value, { maximumFractionDigits: 1, minimumFractionDigits: 1 });
}

function stopPair(s: SlowZoneSegmentData): string {
  return `${s.from_stop_name ?? s.from_stop_id} → ${s.to_stop_name ?? s.to_stop_id}`;
}

/** Every segment of the agency by how far its running time exceeds the
 *  timetable in the newest complete week, and the segments that stayed slow
 *  for weeks. Feed-based estimates, listed by how long they have stayed
 *  slow, not ranked. */
export function SlowZonesView() {
  const { t, i18n } = useTranslation();
  const id = useAgencyId();
  const [band, setBand] = useState<Band>("morning");
  const [dayType, setDayType] = useState<SlowZoneDayType>("weekday");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const query = useSlowZones(id, band, dayType);
  const tier = useCapabilityTier(id);
  const isAdmin = useIsAdmin();
  const data = query.data;
  const segments = data?.segments ?? [];
  const zones = slowZoneSegments(data);
  const selected = segments.find((s) => segmentKey(s) === selectedKey);
  const gap = data ? segmentRuntimeNotShown(t, data, tier, isAdmin) : null;

  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const initialLanguage = useRef(i18n.language);
  const firstStyleRun = useRef(true);
  const [failed, setFailed] = useState(false);
  const [styleEpoch, setStyleEpoch] = useState(0);
  const theme = useThemeSignal();
  const onSegmentClick = useEffectEvent((key: string | null) => setSelectedKey(key));

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
      onSegmentClick(typeof key === "string" ? key : null);
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
      firstStyleRun.current = true;
    };
  }, []);

  // A language change re-labels the basemap. The new style drops the
  // segments' source and layer, which the bumped epoch re-adds.
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
    const collection = segmentFeatures(data);
    return whenStyleReady(instance, () => {
      const source = instance.getSource(SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (source) {
        source.setData(collection);
        return;
      }
      instance.addSource(SOURCE, { type: "geojson", data: collection });
      instance.addLayer({
        id: LAYER,
        type: "line",
        source: SOURCE,
        layout: { "line-cap": "round" },
        paint: linePaint(),
      });
    });
  }, [data, styleEpoch]);

  // Colour tokens resolve when the paint is written, so only a theme change
  // re-writes it.
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    return whenStyleReady(instance, () => {
      if (instance.getLayer(LAYER)) repaintLayer(instance, LAYER, linePaint());
    });
  }, [theme]);

  const bounds = segmentBounds(segments);
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
        { padding: 30, maxZoom: 15, duration: 0 },
      );
    });
  }, [boundsKey]);

  if (id == null) return null;
  const weeks = data?.weeks ?? [];
  const csv = new URLSearchParams({ time_band: band, day_type: dayType, format: "csv" });

  return (
    <section aria-label={t("slow_zones.map_label")} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={CONTROL_ROW}>
        <label style={FIELD}>
          {t("slow_zones.day_type_label")}
          <select value={dayType} onChange={(e) => setDayType(e.target.value as SlowZoneDayType)}>
            {SLOW_ZONE_DAY_TYPES.map((d) => (
              <option key={d} value={d}>
                {t(`slow_zones.day_type.${d}`)}
              </option>
            ))}
          </select>
        </label>
        <label style={FIELD}>
          {t("slow_zones.band_label")}
          <select value={band} onChange={(e) => setBand(e.target.value as Band)}>
            {buildTimeBandOptions(t)
              .filter((option) => option.value !== "all")
              .map(({ value, label }) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
          </select>
        </label>
        <CsvLink href={apiUrl(`/api/${id}/segments/slow-zones?${csv.toString()}`)} />
      </div>
      <p style={MUTED}>{t("slow_zones.estimate_note")}</p>
      {query.error ? <ErrorBanner error={query.error} onRetry={() => void query.refetch()} /> : null}
      {gap && <EmptyState title={gap.status} hint={gap.text} />}
      <Legend />
      <div ref={container} style={{ height: failed ? 0 : 480, borderRadius: 6 }} />
      {failed && <p style={MUTED}>{t("slow_zones.map_unavailable")}</p>}
      {data?.available && weeks.length > 0 && (
        <p style={{ fontSize: "var(--text-sm)" }}>
          {t("slow_zones.summary", {
            segments: segments.length,
            zones: zones.length,
            from: formatDate(weeks[0]),
            to: formatDate(weeks[weeks.length - 1]),
          })}
        </p>
      )}
      {selected && <SegmentDetail segment={selected} />}
      {data?.available && (
        <>
          <h2 style={{ fontSize: "var(--text-md)", margin: "8px 0 0" }}>{t("slow_zones.list_title")}</h2>
          <p style={MUTED}>
            {t("slow_zones.rule", {
              excess: data.thresholds.excess_sec,
              scheduled: data.thresholds.min_scheduled_sec,
              samples: data.thresholds.min_samples,
              weeks: data.thresholds.min_weeks,
            })}
          </p>
          {zones.length === 0 ? (
            <p style={MUTED}>{t("slow_zones.none")}</p>
          ) : (
            <SlowZoneTable zones={zones} weeks={weeks} onSelect={setSelectedKey} />
          )}
        </>
      )}
      <p style={MUTED}>{t("slow_zones.distance_note")}</p>
    </section>
  );
}

function SlowZoneTable({
  zones,
  weeks,
  onSelect,
}: {
  zones: SlowZoneSegmentData[];
  weeks: string[];
  onSelect: (key: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <table style={SHARED_TABLE}>
      <thead>
        <tr>
          <th style={th()}>{t("slow_zones.col.segment")}</th>
          <th style={th()}>{t("slow_zones.col.routes")}</th>
          <th style={th()}>{t("slow_zones.col.since")}</th>
          <th style={th({ align: "right" })}>{t("slow_zones.col.excess")}</th>
          <th style={th({ align: "right" })}>{t("slow_zones.col.speed")}</th>
          <th style={th({ align: "right" })}>{t("slow_zones.col.trips")}</th>
          <th style={th({ align: "right" })}>{t("slow_zones.col.minutes_lost")}</th>
          <th style={th()}>{t("slow_zones.col.weeks")}</th>
        </tr>
      </thead>
      <tbody>
        {zones.map((s) => (
          <tr key={segmentKey(s)}>
            <td style={td()}>
              <button type="button" style={LINK_BUTTON} onClick={() => onSelect(segmentKey(s))}>
                {stopPair(s)}
              </button>
            </td>
            <td style={td()}>{s.routes.join(", ")}</td>
            <td style={td()}>
              {s.slow_since == null
                ? EM_DASH
                : t(streakFillsWindow(s, weeks) ? "slow_zones.since_at_least" : "slow_zones.since", {
                    date: formatDate(s.slow_since),
                    weeks: s.slow_weeks,
                  })}
            </td>
            <td style={td({ align: "right" })}>{seconds(s.latest?.excess_sec)}</td>
            <td style={td({ align: "right" })}>{oneDecimal(s.latest?.speed_kmh)}</td>
            <td style={td({ align: "right" })}>{oneDecimal(s.latest?.trips_per_day)}</td>
            <td style={td({ align: "right" })}>{oneDecimal(s.latest?.minutes_lost_per_day)}</td>
            <td style={td()}>
              <InlineSparkline points={observedExcess(s)} width={80} height={20} drawOn={false} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SegmentDetail({ segment }: { segment: SlowZoneSegmentData }) {
  const { t } = useTranslation();
  const latest: SegmentWeek | null = segment.latest;
  return (
    <p style={{ fontSize: "var(--text-sm)" }}>
      {latest == null
        ? t("slow_zones.detail_unobserved", { pair: stopPair(segment) })
        : t("slow_zones.detail", {
            pair: stopPair(segment),
            routes: segment.routes.join(", "),
            p50: seconds(latest.run_p50_sec),
            p85: seconds(latest.run_p85_sec),
            scheduled: seconds(latest.scheduled_run_sec),
            excess: seconds(latest.excess_sec),
            speed: oneDecimal(latest.speed_kmh),
            distance: segment.distance_m == null ? EM_DASH : formatNumber(segment.distance_m),
            samples: latest.samples,
          })}
    </p>
  );
}

function Legend() {
  const { t } = useTranslation();
  const [e0, e1, e2, e3] = EXCESS_CLASS_EDGES_SEC;
  const items = [
    { label: t("slow_zones.legend.few"), opacity: CLASS_OPACITY[0], width: 3 },
    { label: t("slow_zones.legend.within", { sec: e0 }), opacity: CLASS_OPACITY[1], width: 3 },
    { label: t("slow_zones.legend.range", { from: e0, to: e1 }), opacity: CLASS_OPACITY[2], width: 3 },
    { label: t("slow_zones.legend.range", { from: e1, to: e2 }), opacity: CLASS_OPACITY[3], width: 3 },
    { label: t("slow_zones.legend.range", { from: e2, to: e3 }), opacity: CLASS_OPACITY[4], width: 3 },
    { label: t("slow_zones.legend.over", { sec: e3 }), opacity: CLASS_OPACITY[5], width: 3 },
    { label: t("slow_zones.legend.slow_zone"), opacity: CLASS_OPACITY[5], width: 6 },
  ];
  return (
    <ul style={{ display: "flex", flexWrap: "wrap", gap: 12, listStyle: "none", margin: 0, padding: 0, ...MUTED }}>
      {items.map((item) => (
        <li key={item.label} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <span
            aria-hidden="true"
            style={{ width: 18, height: item.width, borderRadius: 2, background: "var(--accent)", opacity: item.opacity }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}
