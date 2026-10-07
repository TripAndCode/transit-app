import { useRef, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { useUrlState } from "../../api/useUrlState";
import { useAgencyId } from "../../api/useAgencyId";
import { useRouteNames } from "../../api/useRouteNames";
import { routeHref } from "../../routes/destinations";
import { useCappedList } from "../../hooks/useCappedList";
import { useCountUp } from "../../hooks/useCountUp";
import { useFlipRows } from "../../hooks/useFlipRows";
import { delayColor, delayTextColor } from "../../styles/tokens";
import { dowValueLabel } from "../../utils/filterValueLabels";
import { RouteLabel } from "../RouteLabel";
import { COMPARE_AXIS_MAX_MIN, PERIODS, barScale, deltaFor, orderByPeriod, otherPeriod, parseCompareRows, type Period } from "./compareBarMath";
import "./compareBars.css";

const ROWS_CAP = 200;
/** Below this the two periods read as the same; the delta stays neutral. */
const DELTA_DEAD_ZONE_MIN = 0.05;

function Figure({ value }: { value: number }) {
  const shown = useCountUp(value, { decimals: 1 });
  return <>{shown.toFixed(1)}</>;
}

/**
 * The compare report as bars that morph. Switching the period scales every
 * bar from its previous length (transform, never width), re-sorts the rows
 * and lets FLIP carry each to its new rank, while the other period stays as
 * a dashed ghost so the comparison is on screen rather than in memory.
 */
export function CompareBars({ rows }: { rows: readonly unknown[][] }) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const names = useRouteNames(id);
  const { search } = useLocation();
  const [period, setPeriod] = useUrlState<Period>("period", "weekday", PERIODS);
  const listRef = useRef<HTMLDivElement | null>(null);
  const ordered = orderByPeriod(parseCompareRows(rows), period);
  useFlipRows(listRef, ordered.map((r) => r.route_code).join(","));
  const capped = useCappedList(ordered, ROWS_CAP, period);
  const ghost = otherPeriod(period);

  return (
    <div className="compare-bars">
      <div className="compare-bars__head">
        <div className="compare-bars__seg" role="group" aria-label={t("compare.bars.period_label")}>
          {PERIODS.map((p) => (
            <button key={p} type="button" aria-pressed={period === p} onClick={() => setPeriod(p)}>
              {dowValueLabel(p, t)}
            </button>
          ))}
        </div>
        <span className="compare-bars__legend">{t("compare.bars.ghost_legend", { period: dowValueLabel(ghost, t) })}</span>
      </div>
      <div
        ref={listRef}
        className="compare-bars__list"
        role="list"
        aria-label={t("compare.bars.aria", { period: dowValueLabel(period, t), max: COMPARE_AXIS_MAX_MIN })}
      >
        {capped.visible.map((row) => {
          const v = row[period];
          const d = deltaFor(row, period);
          const tone = d == null || Math.abs(d) <= DELTA_DEAD_ZONE_MIN ? "" : d > 0 ? " compare-bar__delta--up" : " compare-bar__delta--down";
          return (
            <div key={row.route_code} role="listitem" className="compare-bar-row" data-flip-key={row.route_code} data-testid="compare-bar-row">
              <span className="compare-bar__route">
                {id != null ? (
                  <Link to={routeHref(id, row.route_code, search)}>
                    <RouteLabel code={row.route_code} names={names} />
                  </Link>
                ) : (
                  <RouteLabel code={row.route_code} names={names} />
                )}
              </span>
              <span className="compare-bar__track" aria-hidden="true">
                <i className="compare-bar__ghost" style={{ "--bar-share": barScale(row[ghost]) } as CSSProperties} />
                <i className="compare-bar__fill" style={{ "--bar-share": barScale(v), background: v == null ? "transparent" : delayColor(v) } as CSSProperties} />
              </span>
              <span className="compare-bar__value num" style={{ color: v == null ? undefined : delayTextColor(v) }}>
                {v == null ? "—" : <Figure value={v} />}
              </span>
              <span className={`compare-bar__delta num${tone}`}>{d == null ? "—" : `${d > 0 ? "+" : ""}${d.toFixed(1)}`}</span>
            </div>
          );
        })}
      </div>
      {capped.remaining > 0 && (
        <button type="button" className="btn-ghost" onClick={capped.showMore}>
          {t("common.show_more", { count: capped.remaining })}
        </button>
      )}
    </div>
  );
}
