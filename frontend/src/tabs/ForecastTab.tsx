import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useForecastHeatmap, useForecastOverview } from "../api/hooks";
import { RoutePickerPill } from "../components/paramPills/RoutePickerPill";
import { OverviewModal } from "../components/OverviewModal";
import { Skeleton } from "../components/Skeleton";
import { ErrorBanner } from "../components/ErrorBanner";
import { delayColor } from "../styles/tokens";
import {
  BAND_ORDER,
  bandOf,
  type Band,
  type ForecastHeatmapCell,
  type ForecastOverviewGridCell,
  type ForecastOverviewRoute,
  type ForecastOverviewWorst,
} from "../api/types";

const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const RAMP_STOPS = 6;

type Tip = { x: number; y: number; text: string } | null;
type View = "hm" | "dow" | "hr" | null;

/** Clickable-card props matching the Overview card pattern (role=button + keyboard). */
function clickable(onClick: () => void) {
  return {
    role: "button",
    tabIndex: 0,
    onClick,
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onClick();
      }
    },
  };
}

function Tooltip({ tip }: { tip: Tip }) {
  if (!tip) return null;
  const x = Math.min(tip.x + 14, window.innerWidth - 170);
  const y = Math.min(tip.y + 14, window.innerHeight - 36);
  return (
    <div
      style={{
        position: "fixed",
        left: x,
        top: y,
        zIndex: 90,
        pointerEvents: "none",
        background: "var(--text-primary)",
        color: "#fff",
        fontSize: 12,
        padding: "5px 9px",
        borderRadius: 6,
        boxShadow: "0 4px 14px rgba(0,0,0,.18)",
        whiteSpace: "nowrap",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {tip.text}
    </div>
  );
}

/** Anchored min→max colour ramp legend (shown inline, not only in the modal). */
function Legend({ min, max, unit }: { min: number; max: number; unit: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 12, fontSize: 11, color: "var(--text-secondary)" }}>
      <span style={{ fontVariantNumeric: "tabular-nums" }}>{min.toFixed(1)}</span>
      <span style={{ display: "inline-flex", gap: 2 }}>
        {Array.from({ length: RAMP_STOPS }, (_, i) => (
          <span key={i} style={{ width: 14, height: 14, borderRadius: 2, background: delayColor(min + ((max - min) * i) / (RAMP_STOPS - 1)) }} />
        ))}
      </span>
      <span style={{ fontVariantNumeric: "tabular-nums" }}>{max.toFixed(1)}</span>
      <span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>{unit}</span>
    </div>
  );
}

/** 7-day × 5-band grid. Dense by construction — used for the agency overview and
 * the per-route detail (route cells collapsed to bands client-side). */
function BandGrid({
  grid,
  bandLabel,
  dayLabel,
  axisMin,
  onTip,
  onLeave,
}: {
  grid: ForecastOverviewGridCell[];
  bandLabel: (b: Band) => string;
  dayLabel: (dow: number) => string;
  axisMin: string;
  onTip: (e: React.MouseEvent, text: string) => void;
  onLeave: () => void;
}) {
  const byKey = new Map(grid.map((c) => [`${c.dow}-${c.band}`, c]));
  const cols = `34px repeat(${BAND_ORDER.length}, 1fr)`;
  return (
    <div onMouseLeave={onLeave}>
      <div style={{ display: "grid", gridTemplateColumns: cols, gap: 4 }}>
        <span />
        {BAND_ORDER.map((b) => (
          <span key={b} style={{ fontSize: 11, color: "var(--text-tertiary)", textAlign: "center" }}>
            {bandLabel(b)}
          </span>
        ))}
        {Array.from({ length: 7 }, (_, di) => {
          const dow = di + 1;
          return [
            <div key={`l${dow}`} style={{ fontSize: 11, color: "var(--text-secondary)", textAlign: "right", paddingRight: 6, display: "flex", alignItems: "center", justifyContent: "flex-end" }}>
              {dayLabel(dow)}
            </div>,
            ...BAND_ORDER.map((b) => {
              const c = byKey.get(`${dow}-${b}`);
              const v = c?.expected_avg_min ?? null;
              const tipText = `${dayLabel(dow)} ${bandLabel(b)} · ${v == null ? "—" : `${v.toFixed(1)}${axisMin}`}`;
              if (v == null) {
                return (
                  <div
                    key={b}
                    data-testid="ov-band-cell"
                    onMouseEnter={(e) => onTip(e, tipText)}
                    onMouseMove={(e) => onTip(e, tipText)}
                    style={{ height: 30, borderRadius: 3, background: "repeating-linear-gradient(45deg,#f0eee9,#f0eee9 3px,#f6f4ef 3px,#f6f4ef 6px)" }}
                  />
                );
              }
              return (
                <div
                  key={b}
                  data-testid="ov-band-cell"
                  onMouseEnter={(e) => onTip(e, tipText)}
                  onMouseMove={(e) => onTip(e, tipText)}
                  style={{ height: 30, borderRadius: 3, background: delayColor(v), opacity: c?.low_confidence ? 0.5 : 1 }}
                />
              );
            }),
          ];
        })}
      </div>
    </div>
  );
}

/** Delay-ranked route list. Bar length encodes delay (not sample volume). */
function RankedRoutes({
  routes,
  axisMin,
  lowConfNote,
  onPick,
}: {
  routes: ForecastOverviewRoute[];
  axisMin: string;
  lowConfNote: string;
  onPick: (code: string) => void;
}) {
  const max = Math.max(...routes.map((r) => r.expected_avg_min), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {routes.map((r) => (
        <div
          key={r.route_code}
          data-testid="ranked-route"
          {...clickable(() => onPick(r.route_code))}
          style={{ display: "grid", gridTemplateColumns: "minmax(120px, 38%) 1fr auto", gap: 10, alignItems: "center", cursor: "pointer", padding: "5px 8px", borderRadius: 6, opacity: r.low_confidence ? 0.6 : 1 }}
        >
          <span style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {r.route_name}
            {r.low_confidence && <small style={{ color: "var(--text-tertiary)", marginLeft: 6 }}>· {lowConfNote}</small>}
          </span>
          <span style={{ display: "block", height: 14, background: "var(--bg-soft)", borderRadius: 3, overflow: "hidden" }}>
            <span style={{ display: "block", height: "100%", width: `${Math.max((r.expected_avg_min / max) * 100, 2)}%`, background: delayColor(r.expected_avg_min), borderRadius: 3 }} />
          </span>
          <b style={{ fontSize: 13, fontVariantNumeric: "tabular-nums", minWidth: 52, textAlign: "right" }}>
            {r.expected_avg_min.toFixed(1)}
            {axisMin}
          </b>
        </div>
      ))}
    </div>
  );
}

function HeatmapGrid({
  cells,
  big,
  axisMin,
  dayLabel,
  ariaLabel,
  onTip,
  onLeave,
}: {
  cells: ForecastHeatmapCell[];
  big: boolean;
  axisMin: string;
  dayLabel: (dow: number) => string;
  ariaLabel?: string;
  onTip: (e: React.MouseEvent, text: string) => void;
  onLeave: () => void;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const byKey = new Map(cells.map((c) => [`${c.dow}-${c.hour}`, c]));
  const labelW = big ? 30 : 22;
  const cellH = big ? 26 : 13;
  const gap = big ? 3 : 2;
  const cols = `${labelW}px repeat(24, 1fr)`;

  return (
    <div role={ariaLabel ? "img" : undefined} aria-label={ariaLabel} onMouseLeave={() => { setHover(null); onLeave(); }}>
      <div style={{ display: "grid", gridTemplateColumns: cols, gap, alignItems: "center" }}>
        {Array.from({ length: 7 }, (_, di) => {
          const dow = di + 1;
          return [
            <div key={`l${dow}`} style={{ fontSize: big ? 11 : 10, color: "var(--text-secondary)", textAlign: "right", paddingRight: 5 }}>
              {dayLabel(dow)}
            </div>,
            ...Array.from({ length: 24 }, (_, h) => {
              const c = byKey.get(`${dow}-${h}`);
              const v = c?.expected_avg_min ?? null;
              const key = `${dow}-${h}`;
              if (v == null || !c) {
                return (
                  <div
                    key={key}
                    onMouseEnter={(e) => onTip(e, `${dayLabel(dow)} ${h}:00 · —`)}
                    onMouseMove={(e) => onTip(e, `${dayLabel(dow)} ${h}:00 · —`)}
                    style={{ height: cellH, borderRadius: 2, background: "repeating-linear-gradient(45deg,#f0eee9,#f0eee9 3px,#f6f4ef 3px,#f6f4ef 6px)" }}
                  />
                );
              }
              const active = hover === key;
              const text = `${dayLabel(dow)} ${h}:00 · ${v.toFixed(1)}${axisMin}`;
              return (
                <div
                  key={key}
                  data-testid="hm-cell"
                  onMouseEnter={(e) => { setHover(key); onTip(e, text); }}
                  onMouseMove={(e) => onTip(e, text)}
                  style={{
                    height: cellH,
                    borderRadius: 2,
                    background: delayColor(v),
                    opacity: c.low_confidence ? 0.5 : 1,
                    outline: active ? "2px solid var(--accent)" : "none",
                    outlineOffset: 1,
                    boxShadow: active ? "0 0 0 3px var(--accent-soft)" : "none",
                  }}
                >
                  {c.low_confidence && <span data-testid="hm-cell-lowconf" hidden />}
                </div>
              );
            }),
          ];
        })}
      </div>
      {big && (
        <div style={{ display: "grid", gridTemplateColumns: cols, gap, marginTop: 5 }}>
          <span />
          {Array.from({ length: 24 }, (_, h) => (
            <span key={h} style={{ fontSize: 10, color: "var(--text-tertiary)", textAlign: "center" }}>
              {h % 6 === 0 ? h : ""}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function MarginBars({
  values,
  labels,
  testid,
  big,
  sparse,
  axisMin,
  onTip,
  onLeave,
}: {
  values: (number | null)[];
  labels: string[];
  testid: string;
  big: boolean;
  sparse: boolean;
  axisMin: string;
  onTip: (e: React.MouseEvent, text: string) => void;
  onLeave: () => void;
}) {
  const max = Math.max(...values.filter((v): v is number => v != null), 1);
  return (
    <div onMouseLeave={onLeave}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: big ? 150 : 64, borderBottom: "1px solid var(--border-soft)" }}>
        {values.map((v, i) =>
          v == null ? (
            <span key={i} style={{ flex: 1 }} />
          ) : (
            <i
              key={i}
              data-testid={testid}
              onMouseEnter={(e) => onTip(e, `${labels[i]} · ${v.toFixed(1)}${axisMin}`)}
              onMouseMove={(e) => onTip(e, `${labels[i]} · ${v.toFixed(1)}${axisMin}`)}
              style={{ flex: 1, display: "block", height: `${Math.max((v / max) * 100, 1)}%`, background: delayColor(v), borderRadius: "3px 3px 0 0" }}
            />
          ),
        )}
      </div>
      <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
        {labels.map((l, i) => (
          <span key={i} style={{ flex: 1, textAlign: "center", fontSize: 10, color: "var(--text-tertiary)" }}>
            {sparse ? (i % 6 === 0 ? i : "") : l}
          </span>
        ))}
      </div>
    </div>
  );
}

function StatStrip({ stats }: { stats: { label: string; value: string }[] }) {
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
      {stats.map((s) => (
        <div key={s.label} style={{ flex: 1, minWidth: 110, background: "var(--bg-soft)", borderRadius: 8, padding: "9px 12px" }}>
          <b style={{ display: "block", fontSize: 16, fontVariantNumeric: "tabular-nums" }}>{s.value}</b>
          <small style={{ fontSize: 10, color: "var(--text-2, var(--text-secondary))" }}>{s.label}</small>
        </div>
      ))}
    </div>
  );
}

function Card({ title, sublabel, action, testid, onOpen, children }: {
  title: string;
  sublabel: string;
  action?: React.ReactNode;
  testid: string;
  onOpen?: () => void;
  children: React.ReactNode;
}) {
  const clickProps = onOpen ? clickable(onOpen) : {};
  return (
    <div className={onOpen ? "ov-card ov-clickable" : "ov-card"} data-testid={testid} aria-label={title} {...clickProps}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 2 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>{title}</span>
        {action}
      </div>
      <p style={{ fontSize: 11, color: "var(--text-tertiary)", margin: "0 0 10px" }}>{sublabel}</p>
      {children}
    </div>
  );
}

/** Collapse a per-route 7×24 heatmap into a 7×5 band grid (sample-weighted). */
function collapseToBands(cells: ForecastHeatmapCell[]): ForecastOverviewGridCell[] {
  const acc = new Map<string, { sum: number; n: number }>();
  for (const c of cells) {
    if (c.expected_avg_min == null || !c.samples) continue;
    const key = `${c.dow}-${bandOf(c.hour)}`;
    const a = acc.get(key) ?? { sum: 0, n: 0 };
    a.sum += c.expected_avg_min * c.samples;
    a.n += c.samples;
    acc.set(key, a);
  }
  const grid: ForecastOverviewGridCell[] = [];
  for (let dow = 1; dow <= 7; dow++) {
    for (const band of BAND_ORDER) {
      const a = acc.get(`${dow}-${band}`);
      const n = a?.n ?? 0;
      grid.push({
        dow,
        band,
        expected_avg_min: a && n ? Math.round((a.sum / n) * 10) / 10 : null,
        samples: n,
        low_confidence: n > 0 && n < 30,
      });
    }
  }
  return grid;
}

export function ForecastTab() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const aid = agencyId ? Number(agencyId) : null;

  const [selectedRoute, setSelectedRoute] = useState<string | null>(null);
  const [tip, setTip] = useState<Tip>(null);
  const [view, setView] = useState<View>(null);
  const [showGrid, setShowGrid] = useState(false);

  // Per-route avg delay for the picker's warm-ramp chips (same query the landing
  // uses — react-query dedupes, so this does not double-fetch).
  const { data: overview } = useForecastOverview(aid);
  const delays = Object.fromEntries((overview?.routes ?? []).map((r) => [r.route_code, r.expected_avg_min]));

  const dayLabel = (dow: number) => t(`forecast.dow_${WEEK[dow - 1]}`);
  const bandLabel = (b: Band) => t(`forecast.band_${b}`);
  const min1 = t("forecast.axis_min");
  const onTip = (e: React.MouseEvent, text: string) => setTip({ x: e.clientX, y: e.clientY, text });
  const onLeave = () => setTip(null);

  return (
    <div style={{ padding: 24, maxWidth: 880, margin: "0 auto" }}>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", letterSpacing: "0.04em" }}>{t("forecast.eyebrow")}</div>
      <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 22, margin: "4px 0 16px" }}>{t("forecast.title")}</h1>

      {aid != null && (
        <div style={{ display: "flex", gap: 10, marginBottom: 22, alignItems: "center", flexWrap: "wrap" }}>
          {selectedRoute && (
            <button
              type="button"
              onClick={() => { setSelectedRoute(null); setShowGrid(false); }}
              style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: 13, padding: 0 }}
            >
              {t("forecast.back_to_overview")}
            </button>
          )}
          <RoutePickerPill label={t("forecast.route_label")} value={selectedRoute} agencyId={aid} placeholder={t("forecast.route_placeholder")} onChange={setSelectedRoute} delays={delays} />
        </div>
      )}

      {aid != null && !selectedRoute && (
        <AgencyLanding
          aid={aid}
          dayLabel={dayLabel}
          bandLabel={bandLabel}
          axisMin={min1}
          worstLabel={t("forecast.overview_worst_label")}
          gridTitle={t("forecast.overview_grid_title")}
          gridCaption={t("forecast.overview_grid_caption")}
          routesTitle={t("forecast.overview_routes_title")}
          routesCaption={t("forecast.overview_routes_caption")}
          noData={t("forecast.overview_no_data")}
          lowConfNote={t("forecast.low_confidence_note")}
          legendUnit={t("forecast.legend_unit")}
          worstPhrase={(w) => t("forecast.overview_worst_phrase", { day: dayLabel(w.dow), band: bandLabel(w.band), min: w.expected_avg_min })}
          onPick={setSelectedRoute}
          onTip={onTip}
          onLeave={onLeave}
        />
      )}

      {aid != null && selectedRoute && (
        <RouteDetail
          aid={aid}
          route={selectedRoute}
          dayLabel={dayLabel}
          bandLabel={bandLabel}
          axisMin={min1}
          showGrid={showGrid}
          onToggleGrid={() => setShowGrid((v) => !v)}
          view={view}
          setView={setView}
          onTip={onTip}
          onLeave={onLeave}
        />
      )}

      <Tooltip tip={tip} />
    </div>
  );
}

function AgencyLanding({
  aid,
  dayLabel,
  bandLabel,
  axisMin,
  worstLabel,
  gridTitle,
  gridCaption,
  routesTitle,
  routesCaption,
  noData,
  lowConfNote,
  legendUnit,
  worstPhrase,
  onPick,
  onTip,
  onLeave,
}: {
  aid: number;
  dayLabel: (dow: number) => string;
  bandLabel: (b: Band) => string;
  axisMin: string;
  worstLabel: string;
  gridTitle: string;
  gridCaption: string;
  routesTitle: string;
  routesCaption: string;
  noData: string;
  lowConfNote: string;
  legendUnit: string;
  worstPhrase: (w: ForecastOverviewWorst) => string;
  onPick: (code: string) => void;
  onTip: (e: React.MouseEvent, text: string) => void;
  onLeave: () => void;
}) {
  const { data, isPending, error, refetch } = useForecastOverview(aid);
  if (isPending) return <Skeleton height={240} />;
  if (error) return <ErrorBanner error={error} onRetry={() => refetch()} />;
  if (!data) return null;

  const populated = data.grid.filter((c) => c.expected_avg_min != null);
  if (populated.length === 0 && data.routes.length === 0) {
    return <p style={{ color: "var(--text-secondary)" }}>{noData}</p>;
  }
  const max = Math.max(...populated.map((c) => c.expected_avg_min as number), 1);
  const min = populated.length ? Math.min(...populated.map((c) => c.expected_avg_min as number)) : 0;

  return (
    <>
      {data.worst && (
        <div data-testid="worst-headline" style={{ background: "var(--bg-soft)", borderRadius: 10, padding: "14px 16px", marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", letterSpacing: "0.04em", marginBottom: 2 }}>{worstLabel}</div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{worstPhrase(data.worst)}</div>
        </div>
      )}

      <Card title={gridTitle} sublabel={gridCaption} testid="fc-overview-grid">
        <BandGrid grid={data.grid} bandLabel={bandLabel} dayLabel={dayLabel} axisMin={axisMin} onTip={onTip} onLeave={onLeave} />
        {populated.length > 0 && <Legend min={min} max={max} unit={legendUnit} />}
      </Card>

      {data.routes.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Card title={routesTitle} sublabel={routesCaption} testid="fc-overview-routes">
            <RankedRoutes routes={data.routes.slice(0, 8)} axisMin={axisMin} lowConfNote={lowConfNote} onPick={onPick} />
          </Card>
        </div>
      )}
    </>
  );
}

function RouteDetail({
  aid,
  route,
  dayLabel,
  bandLabel,
  axisMin,
  showGrid,
  onToggleGrid,
  view,
  setView,
  onTip,
  onLeave,
}: {
  aid: number;
  route: string;
  dayLabel: (dow: number) => string;
  bandLabel: (b: Band) => string;
  axisMin: string;
  showGrid: boolean;
  onToggleGrid: () => void;
  view: View;
  setView: (v: View) => void;
  onTip: (e: React.MouseEvent, text: string) => void;
  onLeave: () => void;
}) {
  const { t } = useTranslation();
  const { data, isPending, error, refetch } = useForecastHeatmap(aid, route);
  const cells = data?.cells ?? [];

  const populated = cells.filter((c) => c.expected_avg_min != null);
  const max = Math.max(...populated.map((c) => c.expected_avg_min as number), 1);
  const min = populated.length ? Math.min(...populated.map((c) => c.expected_avg_min as number)) : 0;
  const totalN = populated.reduce((a, c) => a + c.samples, 0);
  const mean = totalN ? populated.reduce((a, c) => a + (c.expected_avg_min as number) * c.samples, 0) / totalN : 0;
  const allNull = data != null && populated.length === 0;
  const peak = populated.reduce<ForecastHeatmapCell | null>((b, c) => (!b || (c.expected_avg_min as number) > (b.expected_avg_min as number) ? c : b), null);
  const calm = populated.reduce<ForecastHeatmapCell | null>((b, c) => (!b || (c.expected_avg_min as number) < (b.expected_avg_min as number) ? c : b), null);
  // Worst window excluding low-confidence cells, for the headline sentence.
  const worstCell = populated
    .filter((c) => !c.low_confidence)
    .reduce<ForecastHeatmapCell | null>((b, c) => (!b || (c.expected_avg_min as number) > (b.expected_avg_min as number) ? c : b), null);

  const margin = (pick: (c: ForecastHeatmapCell) => number, n: number): (number | null)[] =>
    Array.from({ length: n }, (_, i) => {
      const cs = cells.filter((c) => pick(c) === i && c.expected_avg_min != null);
      const s = cs.reduce((a, c) => a + c.samples, 0);
      return s ? cs.reduce((a, c) => a + (c.expected_avg_min as number) * c.samples, 0) / s : null;
    });
  const dowAvg = margin((c) => c.dow - 1, 7);
  const hourAvg = margin((c) => c.hour, 24);
  const dowLabels = Array.from({ length: 7 }, (_, i) => dayLabel(i + 1));
  const hourLabels = Array.from({ length: 24 }, (_, h) => `${h}:00`);

  const argExtreme = (vals: (number | null)[], worst: boolean) => {
    let idx = -1;
    let best = worst ? -Infinity : Infinity;
    vals.forEach((v, i) => {
      if (v != null && (worst ? v > best : v < best)) {
        best = v;
        idx = i;
      }
    });
    return idx;
  };

  const modalTitle = view === "hm" ? t("forecast.heatmap_title") : view === "dow" ? t("forecast.dow_summary") : t("forecast.hour_summary");

  if (isPending) return <Skeleton height={200} />;
  if (error) return <ErrorBanner error={error} onRetry={() => refetch()} />;
  if (allNull) return <p style={{ color: "var(--text-secondary)" }}>{t("forecast.no_data")}</p>;
  if (!data) return null;

  return (
    <>
      {worstCell && (
        <div data-testid="detail-worst" style={{ fontSize: 15, fontWeight: 600, marginBottom: 14 }}>
          {t("forecast.detail_worst_phrase", { day: dayLabel(worstCell.dow), band: bandLabel(bandOf(worstCell.hour)), min: (worstCell.expected_avg_min as number).toFixed(1) })}
        </div>
      )}

      <Card title={t("forecast.overview_grid_title")} sublabel={t("forecast.heatmap_caption")} testid="fc-detail-bandgrid">
        <BandGrid grid={collapseToBands(cells)} bandLabel={bandLabel} dayLabel={dayLabel} axisMin={axisMin} onTip={onTip} onLeave={onLeave} />
        {populated.length > 0 && <Legend min={min} max={max} unit={t("forecast.legend_unit")} />}
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
        <Card title={t("forecast.dow_summary")} sublabel={t("forecast.click_hint")} action={<span aria-hidden style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{t("forecast.expand")} ⤢</span>} testid="fc-card-dow" onOpen={() => setView("dow")}>
          <MarginBars values={dowAvg} labels={dowLabels} testid="dow-bar" big={false} sparse={false} axisMin={axisMin} onTip={onTip} onLeave={onLeave} />
        </Card>
        <Card title={t("forecast.hour_summary")} sublabel={t("forecast.click_hint")} action={<span aria-hidden style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{t("forecast.expand")} ⤢</span>} testid="fc-card-hr" onOpen={() => setView("hr")}>
          <MarginBars values={hourAvg} labels={hourLabels} testid="hr-bar" big={false} sparse axisMin={axisMin} onTip={onTip} onLeave={onLeave} />
        </Card>
      </div>

      <button
        type="button"
        onClick={onToggleGrid}
        style={{ marginTop: 16, background: "none", border: "1px solid var(--border-soft)", borderRadius: 6, padding: "6px 12px", fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}
      >
        {t("forecast.detail_show_grid")}
      </button>
      {showGrid && (
        <div style={{ marginTop: 14 }} data-testid="fc-detail-fullgrid">
          <HeatmapGrid cells={cells} big axisMin={axisMin} dayLabel={dayLabel} ariaLabel={t("forecast.heatmap_aria")} onTip={onTip} onLeave={onLeave} />
          {populated.length > 0 && <Legend min={min} max={max} unit={t("forecast.legend_unit")} />}
        </div>
      )}

      {view && (
        <OverviewModal isOpen onClose={() => setView(null)} title={modalTitle}>
          {view === "hm" && peak && calm && (
            <>
              <StatStrip
                stats={[
                  { label: t("forecast.stat_worst"), value: `${dayLabel(peak.dow)} ${peak.hour}:00 · ${(peak.expected_avg_min as number).toFixed(1)}${axisMin}` },
                  { label: t("forecast.stat_calmest"), value: `${dayLabel(calm.dow)} ${calm.hour}:00 · ${(calm.expected_avg_min as number).toFixed(1)}${axisMin}` },
                  { label: t("forecast.stat_mean"), value: `${mean.toFixed(1)}${axisMin}` },
                  { label: t("forecast.stat_samples"), value: totalN.toLocaleString() },
                ]}
              />
              <HeatmapGrid cells={cells} big axisMin={axisMin} dayLabel={dayLabel} ariaLabel={t("forecast.heatmap_aria")} onTip={onTip} onLeave={onLeave} />
              <Legend min={min} max={max} unit={t("forecast.legend_unit")} />
            </>
          )}
          {(view === "dow" || view === "hr") && (() => {
            const vals = view === "dow" ? dowAvg : hourAvg;
            const labels = view === "dow" ? dowLabels : hourLabels;
            const wi = argExtreme(vals, true);
            const ci = argExtreme(vals, false);
            return (
              <>
                <StatStrip
                  stats={[
                    { label: t("forecast.stat_worst"), value: wi >= 0 ? `${labels[wi]} · ${(vals[wi] as number).toFixed(1)}${axisMin}` : "—" },
                    { label: t("forecast.stat_calmest"), value: ci >= 0 ? `${labels[ci]} · ${(vals[ci] as number).toFixed(1)}${axisMin}` : "—" },
                    { label: t("forecast.stat_mean"), value: `${mean.toFixed(1)}${axisMin}` },
                    { label: t("forecast.stat_samples"), value: totalN.toLocaleString() },
                  ]}
                />
                <MarginBars values={vals} labels={labels} testid={view === "dow" ? "dow-bar-big" : "hr-bar-big"} big sparse={view === "hr"} axisMin={axisMin} onTip={onTip} onLeave={onLeave} />
              </>
            );
          })()}
          {data.disclaimer && (
            <p style={{ color: "var(--text-tertiary)", fontSize: 11, lineHeight: 1.5, marginTop: 16, borderTop: "1px solid var(--border-soft)", paddingTop: 12 }}>
              {data.disclaimer}
            </p>
          )}
        </OverviewModal>
      )}
    </>
  );
}
