import { useRef, useState, type CSSProperties, type FocusEvent, type KeyboardEvent, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import type { ForecastOverviewGridCell, ForecastOverviewWorst, TrendPayload } from "../../api/types";
import { delayRampVar } from "../../styles/tokens";
import { WEEK } from "../../utils/week";
import { formatMinutes } from "../../utils/format";
import { avgDelayText } from "../../utils/delayPhrase";
import { DOWS, HOURS, forecastSurface, heatSurfaceDimRules, observedSurface, ringFor, surfaceHasData, type Profile } from "./heatSurfaceModel";
import { useTrendFocus } from "./trendFocus";
import "./HeatSurface.css";

const DIM_RULES = heatSurfaceDimRules();
const PROFILES: readonly Profile[] = ["observed", "forecast"];
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
 * cells -- what was observed in the range, and what the forecast expects --
 * and switching between them changes each cell's colour and inset ring in
 * place, so the eye follows the morning peak moving rather than re-reading
 * a new chart.
 *
 * Hover and keyboard focus never re-render it: the handlers write
 * `data-focus-dow` / `data-focus-hour` on the grid (the generated rules in
 * DIM_RULES keep that column and row and recede the rest), copy the cell's
 * label into the readout through a ref, and publish the weekday and hour to
 * the linked trend charts as the `dow` source. As the `dow` viewer, its cells
 * carry `data-mark-dow` and recede through `.focus-dim-opacity` when another
 * chart's focus names a different weekday.
 *
 * The grid is one tab stop (roving tabindex, moved on the DOM rather than in
 * state); arrows, Home and End travel between cells. The grid element itself
 * is focusable only programmatically (tabIndex -1), never a tab stop of its
 * own.
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
  const [profile, setProfile] = useState<Profile>("observed");
  const gridRef = useRef<HTMLDivElement | null>(null);
  const readoutRef = useRef<HTMLParagraphElement | null>(null);
  const observed = observedSurface(hourly);
  const forecast = forecastSurface(grid);
  const surface = profile === "observed" ? observed : forecast;
  const dayLabel = (dow: number) => t(`forecast.dow_${WEEK[dow - 1]}`);
  const cellLabel = (dow: number, hour: number, v: number | null) =>
    v == null
      ? t("reports.heat_surface.readout_empty", { day: dayLabel(dow), hour })
      : t("reports.heat_surface.readout", { day: dayLabel(dow), hour, min: formatMinutes(v) });

  function publish(cell: HTMLElement | null) {
    const node = gridRef.current;
    if (!node) return;
    const dow = cell?.dataset.dow;
    const hour = cell?.dataset.hour;
    if (node.dataset.focusDow === dow && node.dataset.focusHour === hour) return;
    if (dow === undefined || hour === undefined) {
      delete node.dataset.focusDow;
      delete node.dataset.focusHour;
      if (readoutRef.current) readoutRef.current.textContent = t("reports.heat_surface.readout_hint");
      setFocus(null);
      return;
    }
    node.dataset.focusDow = dow;
    node.dataset.focusHour = hour;
    if (readoutRef.current) readoutRef.current.textContent = cell?.getAttribute("aria-label") ?? "";
    setFocus({ source: "dow", dow: Number(dow), hour: Number(hour) });
  }

  function onMouseOver(e: MouseEvent<HTMLDivElement>) {
    publish(cellOf(e.target));
  }
  function onFocus(e: FocusEvent<HTMLDivElement>) {
    const cell = cellOf(e.target);
    if (!cell) return;
    if (cell.tabIndex !== 0) {
      gridRef.current?.querySelector<HTMLElement>('.heat-surface__cell[tabindex="0"]')?.setAttribute("tabindex", "-1");
      cell.tabIndex = 0;
    }
    publish(cell);
  }
  function onBlur(e: FocusEvent<HTMLDivElement>) {
    if (!gridRef.current?.contains(e.relatedTarget as Node | null)) publish(null);
  }
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const cell = cellOf(e.target);
    if (!cell) return;
    const next = step(e.key, Number(cell.dataset.dow), Number(cell.dataset.hour));
    if (!next) return;
    e.preventDefault();
    gridRef.current?.querySelector<HTMLElement>(`.heat-surface__cell[data-dow="${next[0]}"][data-hour="${next[1]}"]`)?.focus();
  }

  const title = <h3 className="heat-surface__title">{t("reports.heat_surface.title")}</h3>;
  if (!surfaceHasData(observed) && !surfaceHasData(forecast)) {
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
          <span>{t("reports.heat_surface.legend_high")}</span>
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
        onMouseLeave={() => publish(null)}
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
                style={{ "--c": v == null ? "var(--none)" : delayRampVar(v), "--ring": ringFor(v) } as CSSProperties}
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
