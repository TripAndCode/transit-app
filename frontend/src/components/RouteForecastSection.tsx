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
 * consequence, not a bug. There is no in-view "back" button: clearing the
 * route chip in the shared Filters bar is the way back, matching how every
 * other tab's focused-route mode already works.
 */
import { useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { useForecastHeatmap, useForecastOverview } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import { InlineSparkline } from "./InlineSparkline";
import { OverviewModal } from "./OverviewModal";
import { Skeleton } from "./Skeleton";
import { ErrorBanner } from "./ErrorBanner";
import { BandGrid, Legend } from "./charts/DowBandGrid";
import { Card } from "./ui/Card";
import { Tooltip } from "./Tooltip";
import { onActivateKey } from "../utils/a11y";
import { delayColor, relativeDelayColor } from "../styles/tokens";
import { Z_INDEX } from "../styles/zIndex";
import { formatNumber } from "../utils/format";
import {
  BAND_ORDER,
  bandOf,
  LOW_CONFIDENCE_SAMPLES,
  type Band,
  type ForecastHeatmapCell,
  type ForecastOverviewGridCell,
  type ForecastOverviewRoute,
  type ForecastOverviewWorst,
} from "../api/types";
import { WEEK } from "../utils/week";

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
  axisMin,
  lowConfNote,
  onPick,
}: {
  routes: ForecastOverviewRoute[];
  axisMin: string;
  lowConfNote: string;
  onPick: (code: string) => void;
}) {
  const vals = routes.map((r) => r.expected_avg_min);
  const max = Math.max(...vals, 1);
  const min = vals.length ? Math.min(...vals) : 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {routes.map((r) => (
        <div
          key={r.route_code}
          data-testid="ranked-route"
          {...clickable(() => onPick(r.route_code))}
          style={{ display: "grid", gridTemplateColumns: "minmax(120px, 34%) 1fr 72px auto", gap: 10, alignItems: "center", cursor: "pointer", padding: "5px 8px", borderRadius: 6, opacity: r.low_confidence ? 0.6 : 1 }}
        >
          <span style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {r.route_name}
            {r.low_confidence && <small style={{ color: "var(--text-tertiary)", marginLeft: 6 }}>· {lowConfNote}</small>}
          </span>
          <span style={{ display: "block", height: 14, background: "var(--bg-soft)", borderRadius: 3, overflow: "hidden" }}>
            <span style={{ display: "block", height: "100%", width: `${Math.max((r.expected_avg_min / max) * 100, 2)}%`, background: relativeDelayColor(r.expected_avg_min, min, max), borderRadius: 3 }} />
          </span>
          {/* Always render this grid cell, even when InlineSparkline itself
              returns null (fewer than 2 points) — otherwise CSS grid
              auto-places the remaining 3 children into columns 1-3,
              shifting the delay number out of its trailing `auto` track. */}
          <span>
            <InlineSparkline points={r.recent_daily ?? []} width={64} height={20} showLabels={false} showEndDot={false} />
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

/**
 * Roving focus over a grid of read-only value cells: the widget is one tab
 * stop and the arrow keys move inside it.
 *
 * A tab stop per cell is the obvious way to make a heatmap keyboard-reachable
 * and the wrong one -- a day x hour grid then sits 168 Tab presses deep in
 * front of everything after it on the page, which is its own barrier. This is
 * the composite-widget pattern ARIA has for that.
 *
 * `slots` is row-major and may hold `null` where a position renders nothing
 * focusable; navigation skips those rather than landing on them.
 */
function useRovingCells(slots: (string | null)[], columns: number) {
  const firstFilled = slots.findIndex((slot) => slot !== null);
  const [requested, setRequested] = useState(firstFilled);
  const containerRef = useRef<HTMLDivElement>(null);
  // Derived, not synchronised: the data can shrink under a held index, and an
  // effect correcting it afterwards would render one frame with no tab stop.
  const active = slots[requested] != null ? requested : firstFilled;

  function step(from: number, delta: number): number | null {
    if (Math.abs(delta) === 1) {
      const row = Math.floor(from / columns);
      for (let i = from + delta; i >= 0 && i < slots.length && Math.floor(i / columns) === row; i += delta) {
        if (slots[i] !== null) return i;
      }
      return null;
    }
    const target = from + delta;
    if (target < 0 || target >= slots.length || slots[target] === null) return null;
    return target;
  }

  function edgeOfRow(from: number, side: "first" | "last"): number | null {
    const row = Math.floor(from / columns);
    const indices = [];
    for (let i = row * columns; i < Math.min((row + 1) * columns, slots.length); i++) {
      if (slots[i] !== null) indices.push(i);
    }
    return (side === "first" ? indices[0] : indices.at(-1)) ?? null;
  }

  function onKeyDown(event: ReactKeyboardEvent) {
    const next =
      event.key === "ArrowRight"
        ? step(active, 1)
        : event.key === "ArrowLeft"
          ? step(active, -1)
          : event.key === "ArrowDown"
            ? step(active, columns)
            : event.key === "ArrowUp"
              ? step(active, -columns)
              : event.key === "Home"
                ? edgeOfRow(active, "first")
                : event.key === "End"
                  ? edgeOfRow(active, "last")
                  : null;
    if (next === null) return;
    event.preventDefault();
    setRequested(next);
    // Focused straight from the handler rather than from an effect on
    // `active`: an effect would also fire on first render and pull focus into
    // the grid before anyone asked for it.
    containerRef.current?.querySelector<HTMLElement>(`[data-cell="${next}"]`)?.focus();
  }

  return { containerRef, activeSlot: slots[active], onKeyDown, onCellFocus: setRequested };
}

function HeatmapGrid({
  cells,
  big,
  axisMin,
  dayLabel,
  ariaLabel,
}: {
  cells: ForecastHeatmapCell[];
  big: boolean;
  axisMin: string;
  dayLabel: (dow: number) => string;
  ariaLabel: string;
}) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<string | null>(null);
  const byKey = new Map(cells.map((c) => [`${c.dow}-${c.hour}`, c]));
  const labelW = big ? 30 : 22;
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
    const head = `${dayLabel(dow)} ${hour}:00 · ${value == null ? "—" : `${value.toFixed(1)}${axisMin}`}`;
    return cell?.low_confidence ? `${head} · ${t("forecast.lowSamples", { count: cell.samples })}` : head;
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
                style={{ fontSize: big ? 11 : 10, color: "var(--text-secondary)", textAlign: "right", paddingRight: 5 }}
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
                return (
                  <Tooltip key={key} label={text}>
                    <div
                      data-testid={empty ? undefined : "hm-cell"}
                      data-cell={index}
                      data-lowconf={cell?.low_confidence ? "" : undefined}
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
                              background:
                                "repeating-linear-gradient(45deg,var(--border-soft),var(--border-soft) 3px,var(--bg-soft) 3px,var(--bg-soft) 6px)",
                              outline: highlighted ? "2px solid var(--accent)" : "none",
                              outlineOffset: 1,
                            }
                          : {
                              position: "relative",
                              height: cellH,
                              borderRadius: 2,
                              background: delayColor(value),
                              opacity: cell.low_confidence ? 0.5 : 1,
                              outline: highlighted ? "2px solid var(--accent)" : "none",
                              outlineOffset: 1,
                              boxShadow: highlighted ? "0 0 0 3px var(--accent-soft)" : "none",
                            }
                      }
                    >
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
            <span key={h} style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)", textAlign: "center" }}>
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
  ariaLabel,
}: {
  values: (number | null)[];
  labels: string[];
  testid: string;
  big: boolean;
  sparse: boolean;
  axisMin: string;
  ariaLabel: string;
}) {
  const max = Math.max(...values.filter((v): v is number => v != null), 1);
  const slots = values.map((v, i) => (v == null ? null : String(i)));
  const { containerRef, activeSlot, onKeyDown, onCellFocus } = useRovingCells(slots, values.length);

  return (
    <div ref={containerRef} role="grid" aria-label={ariaLabel} tabIndex={-1} aria-rowcount={1} onKeyDown={onKeyDown}>
      <div
        role="row"
        style={{ display: "flex", alignItems: "flex-end", gap: 4, height: big ? 150 : 64, borderBottom: "1px solid var(--border-soft)" }}
      >
        {values.map((v, i) => {
          if (v == null) return <span key={i} style={{ flex: 1 }} />;
          const text = `${labels[i]} · ${v.toFixed(1)}${axisMin}`;
          return (
            <Tooltip key={i} label={text}>
              <i
                data-testid={testid}
                data-cell={i}
                role="gridcell"
                aria-label={text}
                tabIndex={activeSlot === String(i) ? 0 : -1}
                onFocus={() => onCellFocus(i)}
                style={{ flex: 1, display: "block", height: `${Math.max((v / max) * 100, 1)}%`, background: delayColor(v), borderRadius: "3px 3px 0 0" }}
              />
            </Tooltip>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 4, marginTop: 4 }} aria-hidden="true">
        {labels.map((l, i) => (
          <span key={i} style={{ flex: 1, textAlign: "center", fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
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
          <small style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}>{s.label}</small>
        </div>
      ))}
    </div>
  );
}

/** Titled surface built on the shared `Card`; `onOpen` makes the whole card a
 *  keyboard-operable control (see `clickable`), matching the Overview card
 *  pattern this was migrated from. */
function SectionCard({ title, sublabel, action, testid, onOpen, children }: {
  title: string;
  sublabel: string;
  action?: React.ReactNode;
  testid: string;
  onOpen?: () => void;
  children: React.ReactNode;
}) {
  const activation = onOpen ? clickable(onOpen) : {};
  return (
    <Card className={onOpen ? "ui-card--clickable" : undefined} data-testid={testid} aria-label={title} {...activation}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 2 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>{title}</span>
        {action}
      </div>
      <p style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)", margin: "0 0 10px" }}>{sublabel}</p>
      {children}
    </Card>
  );
}

/** Filter to cells with a value, plus the min/max of those values (max floored at 1, min at 0 when empty). */
function populatedRange<T extends { expected_avg_min: number | null }>(
  cells: T[],
): { populated: T[]; min: number; max: number } {
  const populated = cells.filter((c) => c.expected_avg_min != null);
  const max = Math.max(...populated.map((c) => c.expected_avg_min as number), 1);
  const min = populated.length ? Math.min(...populated.map((c) => c.expected_avg_min as number)) : 0;
  return { populated, min, max };
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
        low_confidence: n > 0 && n < LOW_CONFIDENCE_SAMPLES,
      });
    }
  }
  return grid;
}

export function RouteForecastSection({ aid }: { aid: number }) {
  const { t } = useTranslation();
  const [ctx, update] = useRangeContext();
  const focusedRoute = ctx.routes.length === 1 ? ctx.routes[0] : null;

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
  const bandLabel = (b: Band) => t(`forecast.band_${b}`);
  const min1 = t("forecast.axis_min");
  // Feeds BandGrid's own cursor-following tip only — see CrosshairTip.
  const onTip = (e: React.MouseEvent, text: string) => setTip({ x: e.clientX, y: e.clientY, text });
  const onLeave = () => setTip(null);

  return (
    <div>
      {!focusedRoute && (
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
          worstPhrase={(w) => t("forecast.overview_worst_phrase", { day: dayLabel(w.dow), band: bandLabel(w.band), min: w.expected_avg_min.toFixed(1) })}
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
          axisMin={min1}
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

/** Agency-wide landing: worst-window headline + day×band grid + delay-ranked routes. */
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

  const { populated, min, max } = populatedRange(data.grid);
  if (populated.length === 0 && data.routes.length === 0) {
    return <p style={{ color: "var(--text-secondary)" }}>{noData}</p>;
  }
  const colorFor = (v: number) => relativeDelayColor(v, min, max);

  return (
    <>
      {data.worst && (
        <div data-testid="worst-headline" style={{ background: "var(--bg-soft)", borderRadius: 10, padding: "14px 16px", marginBottom: 16 }}>
          <div style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)", letterSpacing: "0.04em", marginBottom: 2 }}>{worstLabel}</div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{worstPhrase(data.worst)}</div>
        </div>
      )}

      <SectionCard title={gridTitle} sublabel={gridCaption} testid="fc-overview-grid">
        <BandGrid grid={data.grid} bandLabel={bandLabel} dayLabel={dayLabel} axisMin={axisMin} colorFor={colorFor} onTip={onTip} onLeave={onLeave} />
        {populated.length > 0 && <Legend min={min} max={max} unit={legendUnit} colorFor={colorFor} />}
      </SectionCard>

      {data.routes.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <SectionCard title={routesTitle} sublabel={routesCaption} testid="fc-overview-routes">
            <RankedRoutes routes={data.routes.slice(0, 8)} axisMin={axisMin} lowConfNote={lowConfNote} onPick={onPick} />
          </SectionCard>
        </div>
      )}

      {data.disclaimer && (
        <p style={{ color: "var(--text-tertiary)", fontSize: "var(--text-xs)", lineHeight: 1.5, marginTop: 16 }}>{data.disclaimer}</p>
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

  const { populated, min, max } = populatedRange(cells);
  const totalN = populated.reduce((a, c) => a + c.samples, 0);
  const mean = totalN ? populated.reduce((a, c) => a + (c.expected_avg_min as number) * c.samples, 0) / totalN : 0;
  const allNull = data != null && populated.length === 0;

  // Band-collapsed grid (same banding as the agency landing). The headline worst
  // is the worst *band* (excluding low-confidence) so its number matches the band
  // cell the user sees below it — not a single spiky hour. Its colour ramp is
  // anchored to the band grid's own range.
  const bandGrid = collapseToBands(cells);
  const { populated: bandPop, min: bandMin, max: bandMax } = populatedRange(bandGrid);
  const bandColorFor = (v: number) => relativeDelayColor(v, bandMin, bandMax);
  const worstBand = bandPop
    .filter((c) => !c.low_confidence)
    .reduce<ForecastOverviewGridCell | null>((b, c) => (!b || (c.expected_avg_min as number) > (b.expected_avg_min as number) ? c : b), null);

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

  const modalTitle = view === "dow" ? t("forecast.dow_summary") : t("forecast.hour_summary");

  if (isPending) return <Skeleton height={200} />;
  if (error) return <ErrorBanner error={error} onRetry={() => refetch()} />;
  if (allNull) return <p style={{ color: "var(--text-secondary)" }}>{t("forecast.no_data")}</p>;
  if (!data) return null;

  return (
    <>
      {worstBand && (
        <div
          data-testid="detail-worst"
          style={{ background: "var(--bg-soft)", borderRadius: 10, padding: "14px 16px", marginBottom: 16, fontSize: 15, fontWeight: 600 }}
        >
          {t("forecast.detail_worst_phrase", { day: dayLabel(worstBand.dow), band: bandLabel(worstBand.band), min: (worstBand.expected_avg_min as number).toFixed(1) })}
        </div>
      )}

      <SectionCard title={t("forecast.overview_grid_title")} sublabel={t("forecast.heatmap_caption")} testid="fc-detail-bandgrid">
        <BandGrid grid={bandGrid} bandLabel={bandLabel} dayLabel={dayLabel} axisMin={axisMin} colorFor={bandColorFor} onTip={onTip} onLeave={onLeave} />
        {bandPop.length > 0 && <Legend min={bandMin} max={bandMax} unit={t("forecast.legend_unit")} colorFor={bandColorFor} />}
      </SectionCard>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
        <SectionCard title={t("forecast.dow_summary")} sublabel={t("forecast.click_hint")} action={<span aria-hidden style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>{t("forecast.expand")} ⤢</span>} testid="fc-card-dow" onOpen={() => setView("dow")}>
          <MarginBars values={dowAvg} labels={dowLabels} testid="dow-bar" big={false} sparse={false} axisMin={axisMin} ariaLabel={t("forecast.dow_summary")} />
        </SectionCard>
        <SectionCard title={t("forecast.hour_summary")} sublabel={t("forecast.click_hint")} action={<span aria-hidden style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>{t("forecast.expand")} ⤢</span>} testid="fc-card-hr" onOpen={() => setView("hr")}>
          <MarginBars values={hourAvg} labels={hourLabels} testid="hr-bar" big={false} sparse axisMin={axisMin} ariaLabel={t("forecast.hour_summary")} />
        </SectionCard>
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
          <HeatmapGrid cells={cells} big axisMin={axisMin} dayLabel={dayLabel} ariaLabel={t("forecast.heatmap_aria")} />
          {populated.length > 0 && <Legend min={min} max={max} unit={t("forecast.legend_unit")} />}
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
                    { label: t("forecast.stat_worst"), value: wi >= 0 ? `${labels[wi]} · ${(vals[wi] as number).toFixed(1)}${axisMin}` : "—" },
                    { label: t("forecast.stat_calmest"), value: ci >= 0 ? `${labels[ci]} · ${(vals[ci] as number).toFixed(1)}${axisMin}` : "—" },
                    { label: t("forecast.stat_mean"), value: `${mean.toFixed(1)}${axisMin}` },
                    { label: t("forecast.stat_samples"), value: formatNumber(totalN) },
                  ]}
                />
                <MarginBars values={vals} labels={labels} testid={view === "dow" ? "dow-bar-big" : "hr-bar-big"} big sparse={view === "hr"} axisMin={axisMin} ariaLabel={t(view === "dow" ? "forecast.dow_summary" : "forecast.hour_summary")} />
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
