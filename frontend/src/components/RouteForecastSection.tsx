/**
 * RouteForecastSection — the "Route forecast" entry inside the Analysis tab.
 *
 * Renders the agency-wide forecast landing (worst-window headline, 7-day ×
 * time-band grid pooled across all routes, delay-ranked route list) when no
 * route is focused, or the per-route detail (band-collapsed grid, worst-window
 * sentence, day/hour summaries, full 7×24 grid behind a toggle) when exactly
 * one route is selected via the shared range-context route filter — mirrors
 * MapTab.tsx's own focused-route pattern (self-contained: reads/writes
 * ctx.routes itself, no props needed beyond `aid`). Migrated from the former
 * ForecastTab.tsx; picking a route updates the shared ctx.routes filter, so
 * it also filters every other report type/tab — a deliberate, shared-filter
 * consequence, not a bug. The section's route picker is the way back to all
 * routes, as is clearing the route chip in the shared Filters bar.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useForecastHeatmap, useForecastOverview } from "../api/hooks";
import { useScope } from "../api/scope";
import { useRouteNames } from "../api/useRouteNames";
import { InlineSparkline } from "./InlineSparkline";
import { RouteLabel } from "./RouteLabel";
import { distinctRouteTexts } from "../api/routeDisplayName";
import { OverviewModal } from "./OverviewModal";
import { Skeleton } from "./Skeleton";
import { ErrorBanner } from "./ErrorBanner";
import { BandGrid, EMPTY_CELL_FILL, type TipAnchor } from "./charts/DowBandGrid";
import { DelayScaleLegend } from "./charts/DelayScaleLegend";
import { Card } from "./ui/Card";
import { Tooltip } from "./Tooltip";
import { onActivateKey } from "../utils/a11y";
import { useRovingCells } from "../hooks/useRovingCells";
import { delayRampVar } from "../styles/tokens";
import { Z_INDEX } from "../styles/zIndex";
import { fixedTenth, formatMinutes, formatNumber, roundTenth } from "../utils/format";
import { forecastDelayText } from "../utils/delayPhrase";
import { forecastRangeText } from "../utils/forecastRange";
import { ForecastHistory, ForecastPeriodNote } from "./ForecastDates";
import { datedDayLabel, forecastDayOfMonth } from "../utils/forecastDays";
import { BAND_ORDER, bandLabel as namedBand, bandOf, type Band } from "../api/timeBands";
import {
  LOW_CONFIDENCE_SAMPLES,
  type ForecastHeatmapCell,
  type ForecastOverviewGridCell,
  type ForecastOverviewRoute,
  type ForecastOverviewWorst,
} from "../api/types";
import { WEEK } from "../utils/week";
import "./RouteForecastSection.css";

type Tip = { x: number; y: number; text: string } | null;
type View = "dow" | "hr" | null;


/** Clickable-card props matching the Overview card pattern (role=button + keyboard). */
function clickable(onClick: () => void) {
  return {
    role: "button" as const,
    tabIndex: 0,
    onClick,
    onKeyDown: onActivateKey(() => onClick()),
  };
}

/** Cursor-following readout for BandGrid's dense day×band grid (`DowBandGrid.tsx`,
 *  unaffected by this component's own move to the anchored `Tooltip`): BandGrid
 *  scans many cells under a moving pointer, so its text has to track the cursor
 *  rather than anchor to one cell the way `Tooltip` does. */
function CrosshairTip({ tip }: { tip: Tip }) {
  if (!tip) return null;
  const x = Math.min(tip.x + 14, window.innerWidth - 170);
  const y = Math.min(tip.y + 14, window.innerHeight - 36);
  return (
    <div
      role="tooltip"
      style={{
        position: "fixed",
        left: x,
        top: y,
        zIndex: Z_INDEX.tooltip,
        pointerEvents: "none",
        background: "var(--tooltip-bg)",
        color: "var(--tooltip-fg)",
        fontSize: 12,
        padding: "5px 9px",
        borderRadius: 6,
        boxShadow: "var(--el-2)",
        whiteSpace: "nowrap",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {tip.text}
    </div>
  );
}

/** Delay-ranked route list. Bar length encodes delay (not sample volume). */
function RankedRoutes({
  routes,
  names,
  axisMin,
  lowConfNote,
  onPick,
}: {
  routes: ForecastOverviewRoute[];
  names: ReturnType<typeof useRouteNames>;
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
          style={{ display: "grid", gridTemplateColumns: "minmax(120px, 34%) 1fr 72px auto", gap: 10, alignItems: "center", cursor: "pointer", padding: "5px 8px", borderRadius: 6, opacity: r.low_confidence ? 0.6 : 1 }}
        >
          <span style={{ fontSize: "var(--text-sm)", minWidth: 0 }}>
            <RouteLabel code={r.route_code} names={names} fallbackName={r.route_name} />
            {r.low_confidence && <small style={{ color: "var(--text-tertiary)", marginLeft: 6 }}>· {lowConfNote}</small>}
          </span>
          <span style={{ display: "block", height: 14, background: "var(--bg-soft)", borderRadius: 3, overflow: "hidden" }}>
            <span style={{ display: "block", height: "100%", width: `${Math.max((r.expected_avg_min / max) * 100, 2)}%`, background: delayRampVar(r.expected_avg_min), borderRadius: 3 }} />
          </span>
          {/* Always render this grid cell, even when InlineSparkline itself
              returns null (fewer than 2 points) — otherwise CSS grid
              auto-places the remaining 3 children into columns 1-3,
              shifting the delay number out of its trailing `auto` track. */}
          <span>
            <InlineSparkline points={r.recent_daily ?? []} width={64} height={20} showEndDot={false} />
          </span>
          <b style={{ fontSize: 13, fontVariantNumeric: "tabular-nums", minWidth: 52, textAlign: "right" }}>
            {fixedTenth(r.expected_avg_min)}
            {axisMin}
          </b>
        </div>
      ))}
    </div>
  );
}

/** Row-label width that fits a dated weekday ("10/27(火)", "Tue 10/27") at the grid's label size. */
const DATED_ROW_LABEL_PX = 64;

function HeatmapGrid({
  cells,
  big,
  dayLabel,
  dated,
  markFallback,
  ariaLabel,
}: {
  cells: ForecastHeatmapCell[];
  big: boolean;
  dayLabel: (dow: number) => string;
  /** Some row labels carry a date, so the label column is widened to fit one. */
  dated: boolean;
  /** The model forecasts this view, so a cell showing a past average instead is marked. */
  markFallback: boolean;
  ariaLabel: string;
}) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<string | null>(null);
  const byKey = new Map(cells.map((c) => [`${c.dow}-${c.hour}`, c]));
  const labelW = dated ? DATED_ROW_LABEL_PX : big ? 30 : 22;
  const cellH = big ? 26 : 13;
  const gap = big ? 3 : 2;
  const cols = `${labelW}px repeat(24, 1fr)`;

  const HOURS = 24;
  const slots = Array.from({ length: 7 * HOURS }, (_, i) => `${Math.floor(i / HOURS) + 1}-${i % HOURS}`);
  const { containerRef, activeSlot, onKeyDown, onCellFocus } = useRovingCells(slots, HOURS);

  /** Everything a cell says, in one string. It is the cell's accessible name
   *  and the text of its tooltip; a screen reader reads a name once, where a
   *  second live region carrying the same words reads it twice. */
  function cellText(dow: number, hour: number): string {
    const cell = byKey.get(`${dow}-${hour}`);
    const value = cell?.expected_avg_min;
    const parts = [`${dayLabel(dow)} ${hour}:00`, value == null ? "—" : formatMinutes(value)];
    if (cell?.source === "ml") {
      parts.push(t("forecast.ml_source"));
      const range = forecastRangeText(t, value as number, cell.q10, cell.q90);
      if (range) parts.push(range);
    } else if (markFallback && value != null) {
      parts.push(t("forecast.fallback_cell"));
    }
    if (cell?.low_confidence) parts.push(t("forecast.lowSamples", { count: cell.samples }));
    return parts.join(" · ");
  }

  return (
    <div
      ref={containerRef}
      role="grid"
      aria-label={ariaLabel}
      // The roving cell owns the tab stop; the container is focusable only
      // programmatically, which is what the composite pattern asks for.
      tabIndex={-1}
      aria-rowcount={7}
      aria-colcount={HOURS}
      onKeyDown={onKeyDown}
      onMouseLeave={() => setHover(null)}
    >
      <div style={{ display: "grid", gridTemplateColumns: cols, gap, alignItems: "center" }}>
        {Array.from({ length: 7 }, (_, di) => {
          const dow = di + 1;
          return (
            // `display: contents` so the rows carry the grid semantics while
            // the cells stay direct children of the CSS grid that lays them out.
            <div key={`r${dow}`} role="row" style={{ display: "contents" }}>
              <div
                role="rowheader"
                style={{ fontSize: big ? 11 : 10, color: "var(--text-secondary)", textAlign: "right", paddingRight: 5, whiteSpace: "nowrap" }}
              >
                {dayLabel(dow)}
              </div>
              {Array.from({ length: HOURS }, (_, h) => {
                const key = `${dow}-${h}`;
                const index = di * HOURS + h;
                const cell = byKey.get(key);
                const value = cell?.expected_avg_min ?? null;
                const text = cellText(dow, h);
                const highlighted = hover === key;
                const empty = value == null || !cell;
                const fallback = markFallback && !empty && cell.source !== "ml";
                return (
                  <Tooltip key={key} label={text}>
                    <div
                      data-testid={empty ? undefined : "hm-cell"}
                      data-cell={index}
                      data-lowconf={cell?.low_confidence ? "" : undefined}
                      data-fallback={fallback ? "" : undefined}
                      role="gridcell"
                      aria-label={text}
                      tabIndex={activeSlot === key ? 0 : -1}
                      onMouseEnter={() => setHover(key)}
                      onFocus={() => {
                        setHover(key);
                        onCellFocus(index);
                      }}
                      onMouseLeave={() => setHover(null)}
                      onBlur={() => setHover(null)}
                      style={
                        empty
                          ? {
                              height: cellH,
                              borderRadius: 2,
                              background: EMPTY_CELL_FILL,
                              outline: highlighted ? "2px solid var(--accent)" : "none",
                              outlineOffset: 1,
                            }
                          : {
                              position: "relative",
                              height: cellH,
                              borderRadius: 2,
                              background: delayRampVar(value),
                              opacity: cell.low_confidence ? 0.5 : 1,
                              outline: highlighted ? "2px solid var(--accent)" : "none",
                              outlineOffset: 1,
                              boxShadow: highlighted ? "0 0 0 3px var(--accent-soft)" : "none",
                            }
                      }
                    >
                      {fallback && <span className="fallback-mark" aria-hidden="true" />}
                      {cell?.low_confidence && (
                        // Decoration: the warning is already part of the
                        // cell's own name, and a nested tooltip here would
                        // open alongside the cell's on the way to it.
                        <span
                          data-testid="hm-cell-lowconf"
                          aria-hidden="true"
                          style={{
                            position: "absolute",
                            top: 1,
                            right: 2,
                            fontSize: "var(--text-xs)",
                            fontWeight: 800,
                            lineHeight: 1,
                            color: "var(--color-warning)",
                            pointerEvents: "none",
                          }}
                        >
                          !
                        </span>
                      )}
                    </div>
                  </Tooltip>
                );
              })}
            </div>
          );
        })}
      </div>
      {big && (
        <div style={{ display: "grid", gridTemplateColumns: cols, gap, marginTop: 5 }} aria-hidden="true">
          <span />
          {Array.from({ length: HOURS }, (_, h) => (
            // nowrap: an hour column is narrower than "12" on a phone; the
            // label overflows into its empty neighbours rather than stacking.
            <span key={h} style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)", textAlign: "center", whiteSpace: "nowrap" }}>
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
  axisLabels,
  testid,
  big,
  sparse,
  ariaLabel,
}: {
  values: (number | null)[];
  labels: string[];
  /** What the axis shows under each bar, when it differs from `labels`. */
  axisLabels?: React.ReactNode[];
  testid: string;
  big: boolean;
  sparse: boolean;
  ariaLabel: string;
}) {
  const max = Math.max(...values.filter((v): v is number => v != null), 1);
  const slots = values.map((v, i) => (v == null ? null : String(i)));
  const { containerRef, activeSlot, onKeyDown, onCellFocus } = useRovingCells(slots, values.length);

  return (
    <div ref={containerRef} className={big ? undefined : "fc-bars--compact"} role="grid" aria-label={ariaLabel} tabIndex={-1} aria-rowcount={1} onKeyDown={onKeyDown}>
      <div
        role="row"
        style={{ display: "flex", alignItems: "flex-end", gap: 4, height: big ? 150 : 64, borderBottom: "1px solid var(--border-soft)" }}
      >
        {values.map((v, i) => {
          if (v == null) return <span key={i} style={{ flex: 1 }} />;
          const text = `${labels[i]} · ${formatMinutes(v)}`;
          return (
            <Tooltip key={i} label={text}>
              <i
                data-testid={testid}
                data-cell={i}
                role="gridcell"
                aria-label={text}
                tabIndex={activeSlot === String(i) ? 0 : -1}
                onFocus={() => onCellFocus(i)}
                style={{ flex: 1, display: "block", height: `${Math.max((v / max) * 100, 1)}%`, background: delayRampVar(v), borderRadius: "3px 3px 0 0" }}
              />
            </Tooltip>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 4, marginTop: 4 }} aria-hidden="true">
        {labels.map((l, i) => (
          // nowrap: a column narrower than "12" or "28" must not stack its digits.
          <span key={i} style={{ flex: 1, minWidth: 0, textAlign: "center", fontSize: "var(--text-xs)", color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>
            {sparse ? (i % 6 === 0 ? i : "") : (axisLabels?.[i] ?? l)}
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
          <small style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}>{s.label}</small>
        </div>
      ))}
    </div>
  );
}

/** Titled surface built on the shared `Card`; `onOpen` adds an explicit Expand
 *  button (keyboard and assistive-tech entry point) and makes a pointer click
 *  anywhere on the card open it too. */
function SectionCard({ title, sublabel, testid, onOpen, children }: {
  title: string;
  sublabel: string;
  testid: string;
  onOpen?: () => void;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  // The card is not itself a button: a button's children are presentational,
  // which would hide the focusable grid inside it. Keyboard and assistive-tech
  // users open it from the explicit Expand button; a pointer click anywhere on
  // the card still works as a convenience.
  return (
    <Card className={onOpen ? "ui-card--clickable" : undefined} data-testid={testid} aria-label={title} onClick={onOpen}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 2 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>{title}</span>
        {onOpen && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpen();
            }}
            aria-label={t("forecast.expand_named", { title })}
            style={{ appearance: "none", background: "transparent", border: "none", padding: 0, font: "inherit", fontSize: "var(--text-xs)", color: "var(--text-tertiary)", cursor: "pointer" }}
          >
            {t("forecast.expand")} ⤢
          </button>
        )}
      </div>
      <p style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)", margin: "0 0 10px" }}>{sublabel}</p>
      {children}
    </Card>
  );
}

/** The cells that hold a value. */
function populatedCells<T extends { expected_avg_min: number | null }>(cells: T[]): T[] {
  return cells.filter((c) => c.expected_avg_min != null);
}

/** The shared delay legend, with the hatched fill the forecast gives a
 *  day and time without measurements and, in a view the model forecasts,
 *  the mark on a cell that shows a past average instead. */
function ForecastLegend({ fallback = false }: { fallback?: boolean }) {
  const { t } = useTranslation();
  return (
    <div style={{ marginTop: 12 }}>
      <DelayScaleLegend>
        <span className="delay-scale-legend__item">
          <i aria-hidden="true" style={{ background: EMPTY_CELL_FILL }} />
          {t("delay_scale.no_data")}
        </span>
        {fallback && (
          <span className="delay-scale-legend__item">
            <i aria-hidden="true" className="fallback-mark fallback-mark--legend" />
            {t("forecast.fallback_note")}
          </span>
        )}
      </DelayScaleLegend>
    </div>
  );
}

/**
 * Collapse a per-route 7×24 heatmap into a 7-day × time-band grid by the
 * rule pipeline/reports/forecast.py::summarize_agency_overview applies to the
 * agency grid: every hour of a band counts with the weight of its past
 * observations (`samples`), carrying the forecast's figure where there is one
 * and the past average otherwise, so a band's figure is the one its hours add
 * up to. A forecast hour without past observations weighs nothing, except in
 * a band with nothing else, which then shows the plain mean of its forecasts.
 * A band any weighted forecast reaches is the model's (`source: "ml"`).
 */
function collapseToBands(cells: ForecastHeatmapCell[]): ForecastOverviewGridCell[] {
  type Pool = { sum: number; n: number; ml: boolean; unweighted: number[] };
  const pools = new Map<string, Pool>();
  for (const c of cells) {
    if (c.expected_avg_min == null) continue;
    const key = `${c.dow}-${bandOf(c.hour)}`;
    const pool = pools.get(key) ?? { sum: 0, n: 0, ml: false, unweighted: [] };
    if (c.samples > 0) {
      pool.sum += c.expected_avg_min * c.samples;
      pool.n += c.samples;
      pool.ml ||= c.source === "ml";
    } else if (c.source === "ml") {
      pool.unweighted.push(c.expected_avg_min);
    }
    pools.set(key, pool);
  }
  const grid: ForecastOverviewGridCell[] = [];
  for (let dow = 1; dow <= 7; dow++) {
    for (const band of BAND_ORDER) {
      const pool = pools.get(`${dow}-${band}`);
      const n = pool?.n ?? 0;
      const plain = !n && pool?.unweighted.length ? pool.unweighted.reduce((a, v) => a + v, 0) / pool.unweighted.length : null;
      const ml = n ? !!pool?.ml : plain != null;
      grid.push({
        dow,
        band,
        expected_avg_min: n ? roundTenth((pool as Pool).sum / n) : plain != null ? roundTenth(plain) : null,
        samples: n,
        low_confidence: ml ? n < LOW_CONFIDENCE_SAMPLES : n > 0 && n < LOW_CONFIDENCE_SAMPLES,
        source: ml ? "ml" : "baseline",
      });
    }
  }
  return grid;
}

export function RouteForecastSection({ aid }: { aid: number }) {
  const { t } = useTranslation();
  const [ctx, update] = useScope();
  const focusedRoute = ctx.routes.length === 1 ? ctx.routes[0] : null;
  const names = useRouteNames(aid);

  const [tip, setTip] = useState<Tip>(null);
  const [view, setView] = useState<View>(null);
  const [showGrid, setShowGrid] = useState(false);

  // Reset in-view UI state (not the route selection itself, which is owned
  // by the shared range-context) when the focused route changes — the
  // React "adjust state during render on prop change" pattern, matching
  // MapTab.tsx's own prevFocusedRoute handling.
  const [prevRoute, setPrevRoute] = useState(focusedRoute);
  if (focusedRoute !== prevRoute) {
    setPrevRoute(focusedRoute);
    setView(null);
    setShowGrid(false);
  }

  const dayLabel = (dow: number) => t(`forecast.dow_${WEEK[dow - 1]}`);
  const bandLabel = (b: Band) => namedBand(t, b);
  const min1 = t("forecast.axis_min");
  // Feeds BandGrid's own cursor-following tip only — see CrosshairTip.
  const onTip = (anchor: TipAnchor, text: string) => setTip({ x: anchor.clientX, y: anchor.clientY, text });
  const onLeave = () => setTip(null);

  return (
    <div>
      <RoutePicker aid={aid} names={names} focusedRoute={focusedRoute} onPick={(code) => update({ routes: code ? [code] : null })} />
      <ForecastPeriodNote aid={aid} route={focusedRoute} />
      {!focusedRoute && (
        <AgencyLanding
          aid={aid}
          names={names}
          dayLabel={dayLabel}
          axisMin={min1}
          worstLabel={(source) => t(source === "ml" ? "forecast.overview_worst_label_forecast" : "forecast.overview_worst_label_past")}
          gridTitle={t("forecast.overview_grid_title")}
          gridCaption={t("forecast.overview_grid_caption")}
          routesTitle={t("forecast.overview_routes_title")}
          routesCaption={t("forecast.overview_routes_caption")}
          noData={t("forecast.overview_no_data")}
          lowConfNote={t("forecast.low_confidence_note")}
          worstPhrase={(w, day) => t("forecast.overview_worst_phrase", { day, band: bandLabel(w.band), avg: forecastDelayText(t, w.expected_avg_min, w.source) })}
          onPick={(code) => update({ routes: [code] })}
          onTip={onTip}
          onLeave={onLeave}
        />
      )}

      {focusedRoute && (
        <RouteDetail
          aid={aid}
          route={focusedRoute}
          dayLabel={dayLabel}
          bandLabel={bandLabel}
          showGrid={showGrid}
          onToggleGrid={() => setShowGrid((v) => !v)}
          view={view}
          setView={setView}
          onTip={onTip}
          onLeave={onLeave}
        />
      )}

      <CrosshairTip tip={tip} />
    </div>
  );
}

/** Which route the forecast is for: every route with a forecast, plus a
 *  focused route without one, so the control always states what is shown. */
function RoutePicker({
  aid,
  names,
  focusedRoute,
  onPick,
}: {
  aid: number;
  names: ReturnType<typeof useRouteNames>;
  focusedRoute: string | null;
  onPick: (code: string | null) => void;
}) {
  const { t } = useTranslation();
  const routes = useForecastOverview(aid).data?.routes ?? [];
  // An option holds plain text, so where the route runs, and failing that
  // its route_id, is added only where two routes would otherwise read the same.
  const optionTexts = distinctRouteTexts(routes.map((r) => ({
    key: r.route_code,
    name: names.data.get(r.route_code) ?? r.route_name,
    full: names.parts.has(r.route_code) ? names.describe(r.route_code) : r.route_name,
    id: names.parts.get(r.route_code)?.id ?? r.route_code,
  })));
  const listed = focusedRoute == null || routes.some((r) => r.route_code === focusedRoute);
  return (
    <label className="forecast-route-picker">
      {t("forecast.route_picker_label")}
      <select value={focusedRoute ?? ""} onChange={(e) => onPick(e.currentTarget.value || null)}>
        <option value="">{t("forecast.route_picker_all")}</option>
        {routes.map((r) => (
          <option key={r.route_code} value={r.route_code}>
            {optionTexts.get(r.route_code)}
          </option>
        ))}
        {!listed && <option value={focusedRoute}>{names.data.get(focusedRoute) ?? focusedRoute}</option>}
      </select>
    </label>
  );
}

/** Marks a forecast whose values come from the ML model rather than the
 *  seasonal baseline; rendered once per view, never per cell. */
function MlForecastChip({ aid }: { aid: number }) {
  const { t } = useTranslation();
  return (
    <p className="fc-ml-note" data-testid="fc-ml-chip">
      <span className="caveat-badge">{t("forecast.ml_source")}</span>
      <Link to={`/model?from=${aid}`} className="fc-ml-link">
        {t("forecast.ml_accuracy_link")}
      </Link>
    </p>
  );
}

/** Agency-wide landing: worst-window headline + day×band grid + delay-ranked routes. */
function AgencyLanding({
  aid,
  names,
  dayLabel,
  axisMin,
  worstLabel,
  gridTitle,
  gridCaption,
  routesTitle,
  routesCaption,
  noData,
  lowConfNote,
  worstPhrase,
  onPick,
  onTip,
  onLeave,
}: {
  aid: number;
  names: ReturnType<typeof useRouteNames>;
  dayLabel: (dow: number) => string;
  axisMin: string;
  /** What the headline's window is: the coming week's forecast or the past. */
  worstLabel: (source: "ml" | "baseline") => string;
  gridTitle: string;
  gridCaption: string;
  routesTitle: string;
  routesCaption: string;
  noData: string;
  lowConfNote: string;
  /** `day` names the window's weekday, dated when the model forecast it. */
  worstPhrase: (w: ForecastOverviewWorst, day: string) => string;
  onPick: (code: string) => void;
  onTip: (anchor: TipAnchor, text: string) => void;
  onLeave: () => void;
}) {
  const { t } = useTranslation();
  const { data, isPending, error, refetch } = useForecastOverview(aid);
  if (isPending) return <Skeleton height={240} />;
  if (error) return <ErrorBanner error={error} onRetry={() => refetch()} />;
  if (!data) return null;

  const populated = populatedCells(data.grid);
  if (populated.length === 0 && data.routes.length === 0) {
    return <p style={{ color: "var(--text-secondary)" }}>{noData}</p>;
  }
  const forecastDay = datedDayLabel(t, dayLabel, data.forecast_days);
  const mlBacked = data.grid.some((c) => c.source === "ml");

  const shownRoutes = data.routes.slice(0, 8);
  // InlineSparkline draws nothing from fewer than two days, so the note that
  // explains the line names its window only when some shown route has one.
  const linesDrawn = shownRoutes.some((r) => (r.recent_daily?.length ?? 0) >= 2);
  return (
    <>
      {mlBacked && <MlForecastChip aid={aid} />}
      {data.worst && (
        <div data-testid="worst-headline" style={{ background: "var(--bg-soft)", borderRadius: 10, padding: "14px 16px", marginBottom: 16 }}>
          <div style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)", letterSpacing: "0.04em", marginBottom: 2 }}>{worstLabel(data.worst.source)}</div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{worstPhrase(data.worst, (data.worst.source === "ml" ? forecastDay : dayLabel)(data.worst.dow))}</div>
        </div>
      )}

      <SectionCard title={gridTitle} sublabel={gridCaption} testid="fc-overview-grid">
        <BandGrid ariaLabel={gridTitle} grid={data.grid} dayLabel={forecastDay} markFallback={mlBacked} onTip={onTip} onLeave={onLeave} />
        {populated.length > 0 && <ForecastLegend fallback={mlBacked} />}
      </SectionCard>

      {data.disclaimer && (
        <p style={{ color: "var(--text-tertiary)", fontSize: "var(--text-xs)", lineHeight: 1.5, marginTop: 16 }}>{data.disclaimer}</p>
      )}

      {shownRoutes.length > 0 && (
        <ForecastHistory
          from={data.history_from}
          to={data.history_to}
          recentFrom={linesDrawn ? data.recent_from : null}
          recentTo={linesDrawn ? data.recent_to : null}
        >
          <SectionCard title={routesTitle} sublabel={routesCaption} testid="fc-overview-routes">
            <RankedRoutes routes={shownRoutes} names={names} axisMin={axisMin} lowConfNote={lowConfNote} onPick={onPick} />
          </SectionCard>
        </ForecastHistory>
      )}
    </>
  );
}

/** Per-route detail: band-collapsed grid + worst sentence + day/hour cards, full 7×24 behind a toggle. */
function RouteDetail({
  aid,
  route,
  dayLabel,
  bandLabel,
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
  showGrid: boolean;
  onToggleGrid: () => void;
  view: View;
  setView: (v: View) => void;
  onTip: (anchor: TipAnchor, text: string) => void;
  onLeave: () => void;
}) {
  const { t } = useTranslation();
  const { data, isPending, error, refetch } = useForecastHeatmap(aid, route);
  const cells = data?.cells ?? [];

  const populated = populatedCells(cells);
  const totalN = populated.reduce((a, c) => a + c.samples, 0);
  // An ML cell's `samples` is its hour's past observations, not measurements
  // behind the forecast, so the count is labelled for what it is. The route
  // figure pools the same cells the bars and grid show, ML ones included, so
  // it agrees with them; it is then named as expected, not as an average.
  const mlBacked = populated.some((c) => c.source === "ml");
  const mean = totalN ? populated.reduce((a, c) => a + (c.expected_avg_min as number) * c.samples, 0) / totalN : 0;
  const allNull = data != null && populated.length === 0;

  // The headline names the latest hour first, as its own cell in the full
  // grid shows it, with that cell's range when the model forecast it; then
  // the latest band, as its band cell shows it. Low-confidence cells and
  // bands are passed over in both, so a thin outlier never headlines.
  const bandGrid = collapseToBands(cells);
  const bandPop = populatedCells(bandGrid);
  const forecastDay = datedDayLabel(t, dayLabel, data?.forecast_days ?? []);
  const worstOf = <T extends { expected_avg_min: number | null; low_confidence: boolean }>(xs: T[]) =>
    xs.filter((c) => !c.low_confidence).reduce<T | null>((b, c) => (!b || (c.expected_avg_min as number) > (b.expected_avg_min as number) ? c : b), null);
  const worstBand = worstOf(bandPop);
  const worstCell = worstOf(populated);
  const worstRange = worstCell?.source === "ml" ? forecastRangeText(t, worstCell.expected_avg_min as number, worstCell.q10, worstCell.q90) : null;
  const sourcedDay = (source: "ml" | "baseline" | undefined, dow: number) => (source === "ml" ? forecastDay : dayLabel)(dow);

  const margin = (pick: (c: ForecastHeatmapCell) => number, n: number): (number | null)[] =>
    Array.from({ length: n }, (_, i) => {
      const cs = cells.filter((c) => pick(c) === i && c.expected_avg_min != null);
      const s = cs.reduce((a, c) => a + c.samples, 0);
      return s ? cs.reduce((a, c) => a + (c.expected_avg_min as number) * c.samples, 0) / s : null;
    });
  const dowAvg = margin((c) => c.dow - 1, 7);
  const hourAvg = margin((c) => c.hour, 24);
  const dowLabels = Array.from({ length: 7 }, (_, i) => forecastDay(i + 1));
  // Each day's axis column is narrow, so a forecast day shows its day of the
  // month under the weekday; its tooltip and name carry the whole date.
  const forecastDays = forecastDayOfMonth(data?.forecast_days ?? []);
  const dowAxis = Array.from({ length: 7 }, (_, i) => {
    const day = forecastDays.get(i + 1);
    return day ? (
      <>
        {dayLabel(i + 1)}
        <span className="fc-axis-day">{day}</span>
      </>
    ) : (
      dayLabel(i + 1)
    );
  });
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

  const modalTitle = view === "dow" ? t("forecast.dow_summary") : t("forecast.hour_summary");

  if (isPending) return <Skeleton height={200} />;
  if (error) return <ErrorBanner error={error} onRetry={() => refetch()} />;
  if (allNull) return <p style={{ color: "var(--text-secondary)" }}>{t("forecast.no_data")}</p>;
  if (!data) return null;

  return (
    <>
      {mlBacked && <MlForecastChip aid={aid} />}
      {(worstCell || worstBand) && (
        <div
          data-testid="detail-worst"
          style={{ background: "var(--bg-soft)", borderRadius: 10, padding: "14px 16px", marginBottom: 16, fontSize: 15, fontWeight: 600 }}
        >
          {worstCell && (
            <>
              {t("forecast.detail_worst_hour", { day: sourcedDay(worstCell.source, worstCell.dow), hour: worstCell.hour, avg: forecastDelayText(t, worstCell.expected_avg_min as number, worstCell.source) })}
              {worstRange && <span className="fc-worst-range">{worstRange}</span>}
            </>
          )}
          {worstBand && (
            <span className={worstCell ? "fc-worst-band" : undefined}>
              {t(worstCell ? "forecast.detail_worst_band" : "forecast.detail_worst_band_only", { day: sourcedDay(worstBand.source, worstBand.dow), band: bandLabel(worstBand.band), avg: forecastDelayText(t, worstBand.expected_avg_min as number, worstBand.source) })}
            </span>
          )}
        </div>
      )}

      <SectionCard title={t("forecast.route_grid_title")} sublabel={t("forecast.heatmap_caption")} testid="fc-detail-bandgrid">
        <BandGrid ariaLabel={t("forecast.route_grid_title")} grid={bandGrid} dayLabel={forecastDay} markFallback={mlBacked} onTip={onTip} onLeave={onLeave} />
        {bandPop.length > 0 && <ForecastLegend fallback={mlBacked} />}
      </SectionCard>

      {/* Side by side where both fit, stacked on a phone, where two would
          squeeze the weekday and hour labels together. */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16, marginTop: 16 }}>
        <SectionCard title={t("forecast.dow_summary")} sublabel={t("forecast.click_hint")} testid="fc-card-dow" onOpen={() => setView("dow")}>
          <MarginBars values={dowAvg} labels={dowLabels} axisLabels={dowAxis} testid="dow-bar" big={false} sparse={false} ariaLabel={t("forecast.dow_summary")} />
        </SectionCard>
        <SectionCard title={t("forecast.hour_summary")} sublabel={t("forecast.click_hint")} testid="fc-card-hr" onOpen={() => setView("hr")}>
          <MarginBars values={hourAvg} labels={hourLabels} testid="hr-bar" big={false} sparse ariaLabel={t("forecast.hour_summary")} />
        </SectionCard>
      </div>

      <button
        type="button"
        onClick={onToggleGrid}
        aria-expanded={showGrid}
        style={{ marginTop: 16, background: "none", border: "1px solid var(--border-soft)", borderRadius: 6, padding: "6px 12px", fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}
      >
        {t(showGrid ? "forecast.detail_hide_grid" : "forecast.detail_show_grid")}
      </button>
      {showGrid && (
        <div style={{ marginTop: 14 }} data-testid="fc-detail-fullgrid">
          <HeatmapGrid cells={cells} big dayLabel={forecastDay} dated={forecastDays.size > 0} markFallback={mlBacked} ariaLabel={t("forecast.heatmap_aria")} />
          {populated.length > 0 && <ForecastLegend fallback={mlBacked} />}
        </div>
      )}

      {data.disclaimer && (
        <p style={{ color: "var(--text-tertiary)", fontSize: "var(--text-xs)", lineHeight: 1.5, marginTop: 16 }}>{data.disclaimer}</p>
      )}

      {view && (
        <OverviewModal isOpen onClose={() => setView(null)} title={modalTitle}>
          {(view === "dow" || view === "hr") && (() => {
            const vals = view === "dow" ? dowAvg : hourAvg;
            const labels = view === "dow" ? dowLabels : hourLabels;
            const wi = argExtreme(vals, true);
            const ci = argExtreme(vals, false);
            return (
              <>
                <StatStrip
                  stats={[
                    { label: t(view === "dow" ? "forecast.stat_worst_dow" : "forecast.stat_worst_hour"), value: wi >= 0 ? `${labels[wi]} · ${formatMinutes(vals[wi] as number)}` : "—" },
                    { label: t(view === "dow" ? "forecast.stat_calmest_dow" : "forecast.stat_calmest_hour"), value: ci >= 0 ? `${labels[ci]} · ${formatMinutes(vals[ci] as number)}` : "—" },
                    { label: t(mlBacked ? "forecast.stat_mean_expected" : "forecast.stat_mean"), value: formatMinutes(mean) },
                    { label: t(mlBacked ? "forecast.stat_samples_history" : "forecast.stat_samples"), value: formatNumber(totalN) },
                  ]}
                />
                <MarginBars values={vals} labels={labels} axisLabels={view === "dow" ? dowAxis : undefined} testid={view === "dow" ? "dow-bar-big" : "hr-bar-big"} big sparse={view === "hr"} ariaLabel={t(view === "dow" ? "forecast.dow_summary" : "forecast.hour_summary")} />
              </>
            );
          })()}
          {data.disclaimer && (
            <p style={{ color: "var(--text-tertiary)", fontSize: "var(--text-xs)", lineHeight: 1.5, marginTop: 16, borderTop: "1px solid var(--border-soft)", paddingTop: 12 }}>
              {data.disclaimer}
            </p>
          )}
        </OverviewModal>
      )}
    </>
  );
}
