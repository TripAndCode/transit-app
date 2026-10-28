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
import { delayRampVar } from "../../styles/tokens";
import { serviceValueLabel, type LabelT } from "../../utils/filterValueLabels";
import { RouteLabel } from "../RouteLabel";
import {
  COMPARE_ORDERS,
  PERIODS,
  gapVerdict,
  orderRows,
  otherPeriod,
  parseCompareRows,
  solidSide,
  type CompareOrder,
  type CompareRow,
  type Period,
} from "./compareBarMath";
import { delayAxisMax, delayAxisShare } from "./delayAxis";
import { DelayScaleLegend } from "./DelayScaleLegend";
import "./compareBars.css";
import { fixedTenth } from "../../utils/format";

const ROWS_CAP = 200;

/** The compare report splits days by the agency's day types, so each side
 *  reads as the service value naming it. */
function periodLabel(period: Period, t: LabelT): string {
  return serviceValueLabel(period === "weekday" ? "平日" : "土日祝", t); // i18n-ignore: query contract
}

function orderLabel(order: CompareOrder, t: LabelT): string {
  return order === "gap" ? t("compare.bars.order_gap") : t("compare.bars.order_side", { side: periodLabel(order, t) });
}

function Figure({ value }: { value: number }) {
  const shown = useCountUp(value, { decimals: 1 });
  return <>{fixedTenth(shown)}</>;
}

function GapText({ row }: { row: CompareRow }) {
  const { t } = useTranslation();
  const verdict = gapVerdict(row);
  if (verdict == null) return <>—</>;
  if (verdict.later == null) return <>{t("compare.bars.gap_even")}</>;
  return <>{t("compare.bars.gap_later", { side: periodLabel(verdict.later, t), minutes: fixedTenth(verdict.minutes) })}</>;
}

/**
 * The compare report as bars against one shared axis. Each route shows both
 * sides' figures and says which side runs later and by how much, so the gap,
 * the report's subject, is read rather than worked out. The list is ranked
 * by that gap unless the reader ranks it by one side's level; that side is
 * then the solid bar (switching slides each bar by translateX, never width or
 * scaleX, and FLIP carries each row to its new rank) and the other stays as a
 * dashed ghost. The axis is stepped from the largest figure on either side,
 * so no order switch rescales it.
 *
 * `resetKey` names the list (its scope): "show more" holds across an order
 * switch, which re-ranks the same routes, and resets for a different list.
 */
export function CompareBars({ rows, resetKey }: { rows: readonly unknown[][]; resetKey: string }) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const names = useRouteNames(id);
  const { search } = useLocation();
  const [order, setOrder] = useUrlState<CompareOrder>("rank", "gap", COMPARE_ORDERS);
  const listRef = useRef<HTMLDivElement | null>(null);
  const parsed = parseCompareRows(rows);
  const axisMax = delayAxisMax(parsed.flatMap((r) => [r.weekday, r.weekend]));
  const ordered = orderRows(parsed, order);
  useFlipRows(listRef, ordered.map((r) => r.route_code).join(","));
  const capped = useCappedList(ordered, ROWS_CAP, resetKey);
  const solid = solidSide(order);
  const ghost = otherPeriod(solid);

  return (
    <div className="compare-bars">
      <div className="compare-bars__head">
        <div className="compare-bars__seg" role="group" aria-label={t("compare.bars.order_label")}>
          {COMPARE_ORDERS.map((o) => (
            <button key={o} type="button" aria-pressed={order === o} onClick={() => setOrder(o)}>
              {orderLabel(o, t)}
            </button>
          ))}
        </div>
        <span className="compare-bars__legend" data-testid="compare-bars-legend">
          {t("compare.bars.legend", { solid: periodLabel(solid, t), ghost: periodLabel(ghost, t), max: axisMax })}
        </span>
      </div>
      <DelayScaleLegend />
      {/* The headings, drawn on the rows' grid; each row's own cells carry
          their side's name for a screen reader. */}
      <div className="compare-bar-row compare-bar-row--head" data-testid="compare-bars-head" aria-hidden="true">
        <span>{t("compare.bars.col_route")}</span>
        <span />
        {PERIODS.map((p) => (
          <span key={p} className="compare-bar__value">
            {t("compare.bars.col_side", { side: periodLabel(p, t) })}
          </span>
        ))}
        <span className="compare-bar__gap">{t("compare.bars.col_gap")}</span>
      </div>
      <div
        ref={listRef}
        className="compare-bars__list"
        role="list"
        aria-label={t("compare.bars.aria", { order: orderLabel(order, t), max: axisMax })}
      >
        {capped.visible.map((row) => {
          const v = row[solid];
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
                <i
                  className="compare-bar__fill"
                  style={{ "--bar-share": delayAxisShare(v, axisMax), background: v == null ? "transparent" : delayRampVar(v) } as CSSProperties}
                />
                {/* After the fill, so its dashed edge paints over a longer fill. */}
                <i className="compare-bar__ghost" style={{ "--bar-share": delayAxisShare(row[ghost], axisMax) } as CSSProperties} />
              </span>
              {PERIODS.map((p) => {
                const value = row[p];
                return (
                  <span
                    key={p}
                    className={`compare-bar__value num${p === solid ? " compare-bar__value--solid" : ""}`}
                    data-testid={`compare-bar-${p}`}
                  >
                    <span className="compare-bars__sr">{periodLabel(p, t)} </span>
                    {value == null ? "—" : <Figure value={value} />}
                  </span>
                );
              })}
              <span className="compare-bar__gap" data-testid="compare-bar-gap">
                <GapText row={row} />
              </span>
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
