import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { useRovingCells } from "../../hooks/useRovingCells";
import { delayRampVar } from "../../styles/tokens";
import type { ForecastOverviewGridCell } from "../../api/types";
import { BAND_ORDER, bandHoursText, bandLabel, bandName } from "../../api/timeBands";
import { useFirstData } from "../../hooks/useFirstData";
import { staggerDelay } from "./ChartEnter";
import { formatMinutes } from "../../utils/format";
import "./DowBandGrid.css";

/** Where a tooltip is anchored: a pointer position, or the cell's own edge for a
 *  keyboard focus, which has no pointer. */
export type TipAnchor = { clientX: number; clientY: number };

/** A day and time without measurements, hatched so it never reads as a calm cell. */
export const EMPTY_CELL_FILL =
  "repeating-linear-gradient(45deg,var(--border-soft),var(--border-soft) 3px,var(--bg-soft) 3px,var(--bg-soft) 6px)";

/** 7-day × time-band grid, one column per band of `BAND_ORDER`. Dense by
 * construction — used for the agency overview and the per-route detail (route
 * cells collapsed to bands client-side). Each column is headed by its band's
 * name over its hours; a phone column is too narrow for a name, so there it
 * shows the hours alone, and every cell still names its band in full.
 *
 * Cells are shaded by the shared delay steps (`delayRampVar`), with no
 * outline over the heavy ones; `DelayScaleLegend` labels the steps. */
export function BandGrid({
  ariaLabel,
  grid,
  dayLabel,
  onTip,
  onLeave,
}: {
  /** Already-translated accessible name for the grid. */
  ariaLabel: string;
  grid: ForecastOverviewGridCell[];
  dayLabel: (dow: number) => string;
  onTip: (anchor: TipAnchor, text: string) => void;
  onLeave: () => void;
}) {
  const { t } = useTranslation();
  const byKey = new Map(grid.map((c) => [`${c.dow}-${c.band}`, c]));
  const cols = `auto repeat(${BAND_ORDER.length}, 1fr)`;
  const entered = useFirstData(grid.length > 0);
  // The cell under the pointer or holding keyboard focus, outlined so a sighted
  // keyboard user sees which cell the tooltip is about.
  const [active, setActive] = useState<number | null>(null);
  // Every cell is focusable, so a keyboard or screen-reader user reads the
  // same value a pointer user gets from the tooltip, with one tab stop for
  // the whole grid.
  const slots = Array.from({ length: 7 * BAND_ORDER.length }, (_, i) => String(i));
  const { containerRef, activeSlot, onKeyDown, onCellFocus } = useRovingCells(slots, BAND_ORDER.length);
  // .chart-cell-opacity gives a reduced-motion viewer (who gets none of the
  // entrance classes) the same --cell-opacity the fade would have landed on,
  // so the low-confidence dimming still applies.
  const cellClass = `chart-cell-opacity chart-cell-enter${entered ? " chart-cell-enter--in" : ""}`;
  return (
    <div
      ref={containerRef}
      role="grid"
      aria-label={ariaLabel}
      // The roving cell owns the tab stop; the container is focusable only
      // programmatically, which is what the composite pattern asks for.
      tabIndex={-1}
      // The counts include the header row and the weekday-label column.
      aria-rowcount={7 + 1}
      aria-colcount={BAND_ORDER.length + 1}
      onKeyDown={onKeyDown}
      onMouseLeave={() => {
        setActive(null);
        onLeave();
      }}
    >
      <div style={{ display: "grid", gridTemplateColumns: cols, gap: 4 }}>
        {/* `display: contents` so the rows carry the grid semantics while the
            cells stay direct children of the CSS grid that lays them out. */}
        <div role="row" style={{ display: "contents" }}>
          <span aria-hidden="true" />
          {BAND_ORDER.map((b) => (
            <span key={b} role="columnheader" aria-label={bandLabel(t, b)} className="band-grid__head">
              <span className="band-grid__name" aria-hidden="true">{bandName(t, b)}</span>
              <span className="band-grid__hours" aria-hidden="true">{bandHoursText(t, b)}</span>
            </span>
          ))}
        </div>
        {Array.from({ length: 7 }, (_, di) => {
          const dow = di + 1;
          return (
            <div key={`r${dow}`} role="row" style={{ display: "contents" }}>
            <div role="rowheader" style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)", textAlign: "right", paddingRight: 6, display: "flex", alignItems: "center", justifyContent: "flex-end", whiteSpace: "nowrap" }}>
              {dayLabel(dow)}
            </div>
            {BAND_ORDER.map((b, bi) => {
              const c = byKey.get(`${dow}-${b}`);
              const v = c?.expected_avg_min ?? null;
              const tipText = `${dayLabel(dow)} ${bandLabel(t, b)} · ${v == null ? "—" : formatMinutes(v)}`;
              // The dimming for a low-confidence cell (--cell-opacity) has to
              // come from a CSS custom property, not a plain inline
              // `opacity` -- an inline style always wins over the
              // .chart-cell-enter class's own opacity rule, which would
              // permanently pin every cell at its final value and leave
              // nothing for the fade-in to animate.
              const targetOpacity = c?.low_confidence ? 0.5 : 1;
              const staggerStyle = { ...staggerDelay(di * BAND_ORDER.length + bi), "--cell-opacity": targetOpacity } as CSSProperties;
              const index = di * BAND_ORDER.length + bi;
              const showTip = (e: React.MouseEvent) => {
                setActive(index);
                onTip(e, tipText);
              };
              const highlight = active === index ? { outline: "2px solid var(--accent)", outlineOffset: 1 } : null;
              const cellA11y = {
                role: "gridcell",
                "aria-label": tipText,
                "data-cell": index,
                tabIndex: activeSlot === String(index) ? 0 : -1,
                onFocus: (e: React.FocusEvent<HTMLElement>) => {
                  onCellFocus(index);
                  setActive(index);
                  const box = e.currentTarget.getBoundingClientRect();
                  onTip({ clientX: box.left + box.width / 2, clientY: box.top + box.height / 2 }, tipText);
                },
                onBlur: () => {
                  setActive(null);
                  onLeave();
                },
              };
              if (v == null) {
                return (
                  <div
                    key={b}
                    data-testid="ov-band-cell"
                    {...cellA11y}
                    className={cellClass}
                    onMouseEnter={showTip}
                    onMouseMove={showTip}
                    style={{
                      height: 30,
                      borderRadius: 3,
                      background: EMPTY_CELL_FILL,
                      ...highlight,
                      ...staggerStyle,
                    }}
                  />
                );
              }
              return (
                <div
                  key={b}
                  data-testid="ov-band-cell"
                  {...cellA11y}
                  className={cellClass}
                  onMouseEnter={showTip}
                  onMouseMove={showTip}
                  style={{ height: 30, borderRadius: 3, background: delayRampVar(v), ...highlight, ...staggerStyle }}
                />
              );
            })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
