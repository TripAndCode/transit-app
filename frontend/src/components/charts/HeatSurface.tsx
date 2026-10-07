import { useRef, useState, type CSSProperties, type FocusEvent, type KeyboardEvent, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import type { ForecastOverviewGridCell, ForecastOverviewWorst, TrendPayload } from "../../api/types";
import { DELAY_THRESHOLDS, delayRampVar } from "../../styles/tokens";
import { WEEK } from "../../utils/week";
import { formatMinutes } from "../../utils/format";
import { avgDelayText } from "../../utils/delayPhrase";
import { DOWS, HOURS, THIN_OPACITY, bandSurface, bandThin, heatSurfaceDimRules, observedSurface, observedThin, ringFor, surfaceHasData, type Profile } from "./heatSurfaceModel";
import { useTrendFocus } from "./trendFocus";
import "./HeatSurface.css";

const DIM_RULES = heatSurfaceDimRules();
const PROFILES: readonly Profile[] = ["hourly", "banded"];
const RAMP = ["--d0", "--d1", "--d2", "--d3", "--d4"] as const;

function cellOf(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element ? target.closest<HTMLElement>(".heat-surface__cell") : null;
}

/** Where an arrow, Home or End key moves from (dow, hour); null for any other key. */
function step(key: string, dow: number, hour: number): [number, number] | null {
  switch (key) {
    case "ArrowRight": return [dow, Math.min(HOURS - 1, hour + 1)];
    case "ArrowLeft": return [dow, Math.max(0, hour - 1)];
    case "ArrowDown": return [Math.min(DOWS, dow + 1), hour];
    case "ArrowUp": return [Math.max(1, dow - 1), hour];
    case "Home": return [dow, 0];
    case "End": return [dow, HOURS - 1];
    default: return null;
  }
}

/**
 * The trend view's weekday × hour surface. Two profiles over the same 168
 * cells, both of the selected range -- its delay hour by hour, and the same
 * delay averaged by time band -- and switching between them changes each
 * cell's colour and inset ring in place, so the eye follows the morning peak
 * spreading across its band rather than re-reading a new chart. A cell
 * pooled from few observations is drawn faint (--mark-opacity).
 *
 * Hover and keyboard focus never re-render it: the handlers write
 * `data-focus-dow` / `data-focus-hour` on the grid (the generated rules in
 * DIM_RULES keep that column and row and recede the rest), copy the cell's
 * label into the readout through a ref, and publish the weekday to the
 * linked trend charts as the `dow` source. As the `dow` viewer, its cells
 * carry `data-mark-dow` and recede through `.focus-dim-opacity` when another
 * chart's focus names a different weekday.
 *
 * The grid is one tab stop (roving tabindex, moved on the DOM rather than in
 * state); arrows, Home and End travel between cells. The grid element itself
 * is focusable only programmatically (tabIndex -1), and passes any focus it
 * receives on to the cell holding the tab stop.
 */
export function HeatSurface({
  hourly,
  grid,
  worst,
  rangeDays,
}: {
  hourly: TrendPayload["hourly"];
  grid: ForecastOverviewGridCell[];
  worst: ForecastOverviewWorst | null;
  rangeDays: number;
}) {
  const { t } = useTranslation();
  const { setFocus } = useTrendFocus();
  const [profile, setProfile] = useState<Profile>("hourly");
  const gridRef = useRef<HTMLDivElement | null>(null);
  const readoutRef = useRef<HTMLParagraphElement | null>(null);
  // Which cell the pointer is over, and whether the focused cell was reached
  // by keyboard: the pointer leaving falls back to keyboard focus only, and
  // keyboard focus leaving falls back to the hovered cell. A click focuses a
  // cell too, but that focus shows no ring and must not hold the narrowing.
  const hoveredRef = useRef<HTMLElement | null>(null);
  const pointerPressRef = useRef(false);
  const keyboardFocusRef = useRef(false);
  const observed = observedSurface(hourly);
  const banded = bandSurface(grid, observed);
  const surface = profile === "hourly" ? observed : banded;
  const thin = profile === "hourly" ? observedThin(hourly) : bandThin(grid, observed);
  const dayLabel = (dow: number) => t(`forecast.dow_${WEEK[dow - 1]}`);
  const cellLabel = (dow: number, hour: number, v: number | null) =>
    v == null
      ? t("reports.heat_surface.readout_empty", { day: dayLabel(dow), hour })
      : t("reports.heat_surface.readout", { day: dayLabel(dow), hour, min: formatMinutes(v) });

  /** Shows `cell` on the grid and readout, and names its weekday to the
   *  linked charts every time: another chart may have replaced or cleared
   *  the shared focus since this grid last wrote its own. */
  function publish(cell: HTMLElement | null) {
    const node = gridRef.current;
    if (!node) return;
    const dow = cell?.dataset.dow;
    const hour = cell?.dataset.hour;
    if (dow === undefined || hour === undefined) {
      delete node.dataset.focusDow;
      delete node.dataset.focusHour;
      if (readoutRef.current) readoutRef.current.textContent = t("reports.heat_surface.readout_hint");
      setFocus(null);
      return;
    }
    if (node.dataset.focusDow !== dow || node.dataset.focusHour !== hour) {
      node.dataset.focusDow = dow;
      node.dataset.focusHour = hour;
    }
    // Compared on content, not coordinates: a profile switch relabels the same cell.
    const label = cell?.getAttribute("aria-label") ?? "";
    if (readoutRef.current && readoutRef.current.textContent !== label) readoutRef.current.textContent = label;
    setFocus({ source: "dow", dow: Number(dow) });
  }

  /** The cell keyboard focus rests on, which the pointer leaving falls back to. */
  function keyboardFocusedCell(): HTMLElement | null {
    const active = document.activeElement;
    if (!keyboardFocusRef.current || !gridRef.current || !active || !gridRef.current.contains(active)) return null;
    return cellOf(active);
  }

  function onMouseOver(e: MouseEvent<HTMLDivElement>) {
    const cell = cellOf(e.target);
    if (!cell) return;
    hoveredRef.current = cell;
    publish(cell);
  }
  function onMouseLeave() {
    hoveredRef.current = null;
    publish(keyboardFocusedCell());
  }
  function onFocus(e: FocusEvent<HTMLDivElement>) {
    keyboardFocusRef.current = !pointerPressRef.current;
    pointerPressRef.current = false;
    const cell = cellOf(e.target);
    if (!cell) {
      // The grid itself took focus (a press between cells): hand it to the
      // cell that holds the tab stop, so the arrows keep working.
      if (e.target === gridRef.current) gridRef.current.querySelector<HTMLElement>('.heat-surface__cell[tabindex="0"]')?.focus();
      return;
    }
    if (cell.tabIndex !== 0) {
      gridRef.current?.querySelector<HTMLElement>('.heat-surface__cell[tabindex="0"]')?.setAttribute("tabindex", "-1");
      cell.tabIndex = 0;
    }
    publish(cell);
  }
  function onBlur(e: FocusEvent<HTMLDivElement>) {
    if (gridRef.current?.contains(e.relatedTarget as Node | null)) return;
    keyboardFocusRef.current = false;
    publish(hoveredRef.current);
  }
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const cell = cellOf(e.target);
    // Alt/Cmd + arrow is the browser's history navigation, not a move.
    if (!cell || e.altKey || e.metaKey) return;
    keyboardFocusRef.current = true;
    const next = step(e.key, Number(cell.dataset.dow), Number(cell.dataset.hour));
    if (!next) return;
    e.preventDefault();
    gridRef.current?.querySelector<HTMLElement>(`.heat-surface__cell[data-dow="${next[0]}"][data-hour="${next[1]}"]`)?.focus();
  }

  const title = <h3 className="heat-surface__title">{t("reports.heat_surface.title")}</h3>;
  if (!surfaceHasData(observed)) {
    return (
      <div className="heat-surface-card">
        {title}
        <p className="heat-surface__empty">{t("reports.dow_band.empty")}</p>
      </div>
    );
  }

  return (
    <div className="heat-surface-card">
      <style>{DIM_RULES}</style>
      <div className="heat-surface__head">
        {title}
        <div className="heat-surface__seg" role="group" aria-label={t("reports.heat_surface.profile_label")}>
          {PROFILES.map((p) => (
            <button key={p} type="button" aria-pressed={profile === p} onClick={() => setProfile(p)}>
              {t(`reports.heat_surface.profile_${p}`)}
            </button>
          ))}
        </div>
        <div className="heat-surface__legend" aria-hidden="true">
          <span>{t("reports.heat_surface.legend_low")}</span>
          <span className="heat-surface__ramp">
            {RAMP.map((v) => (
              <i key={v} style={{ background: `var(${v})` }} />
            ))}
          </span>
          <span>{t("reports.heat_surface.legend_high", { min: DELAY_THRESHOLDS.severe })}</span>
        </div>
      </div>
      {worst && (
        <p className="heat-surface__worst">
          {t("reports.dow_band.worst_phrase", {
            count: rangeDays,
            day: dayLabel(worst.dow),
            band: t(`forecast.band_${worst.band}`),
            avg: avgDelayText(t, worst.expected_avg_min),
          })}
        </p>
      )}
      <div
        ref={gridRef}
        className="heat-surface"
        role="grid"
        tabIndex={-1}
        aria-label={t("reports.heat_surface.aria")}
        data-focus-viewer="dow"
        onMouseOver={onMouseOver}
        onPointerDown={() => {
          pointerPressRef.current = true;
        }}
        onMouseLeave={onMouseLeave}
        onFocus={onFocus}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
      >
        <div className="heat-surface__row" aria-hidden="true">
          <span />
          {Array.from({ length: HOURS }, (_, h) => (
            <span key={h} className="heat-surface__hl num">
              {h}
            </span>
          ))}
        </div>
        {surface.map((row, di) => (
          <div key={di} className="heat-surface__row" role="row">
            <span className="heat-surface__lab" aria-hidden="true">
              {dayLabel(di + 1)}
            </span>
            {row.map((v, h) => (
              <div
                key={h}
                role="gridcell"
                tabIndex={di === 0 && h === 0 ? 0 : -1}
                className="heat-surface__cell focus-dim-opacity"
                data-dow={di + 1}
                data-mark-dow={di + 1}
                data-hour={h}
                aria-label={cellLabel(di + 1, h, v)}
                style={
                  {
                    "--c": v == null ? "var(--none)" : delayRampVar(v),
                    "--ring": ringFor(v),
                    ...(thin[di][h] ? { "--mark-opacity": THIN_OPACITY } : {}),
                  } as CSSProperties
                }
              />
            ))}
          </div>
        ))}
      </div>
      <p ref={readoutRef} className="heat-surface__readout" data-testid="heat-surface-readout" aria-hidden="true">
        {t("reports.heat_surface.readout_hint")}
      </p>
    </div>
  );
}
