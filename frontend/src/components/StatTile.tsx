import { useCountUp } from "../hooks/useCountUp";
import { formatNumber } from "../utils/format";
import "./StatTile.css";

type Props = {
  label: string;
  /** A number counts up (via useCountUp) and is formatted with
   *  `formatNumber()`; a pre-formatted string (e.g. one that
   *  already carries a unit the caller controls) renders as-is, unanimated. */
  value: string | number;
  flagged?: boolean;
  /** Appended after a numeric `value`'s formatted digits, e.g. "%". Ignored
   *  for a string `value`, which already carries its own suffix. */
  suffix?: string;
  /** Decimal places for a numeric `value`. */
  decimals?: number;
};

/** Small bordered stat card: a label over a large number. `flagged` colors
 *  the value --delay-flag instead of --accent-strong for "needs attention"
 *  counts (e.g. a nonzero 5-minutes-plus-delay count) -- callers decide
 *  what counts as flagged, this component only renders the two states. */
export function StatTile({ label, value, flagged, suffix = "", decimals = 0 }: Props) {
  const numericTarget = typeof value === "number" ? value : null;
  const counted = useCountUp(numericTarget ?? 0, { decimals });
  const text =
    numericTarget != null
      ? `${formatNumber(counted, {
          minimumFractionDigits: decimals,
          maximumFractionDigits: decimals,
        })}${suffix}`
      : value;
  return (
    <div className="stat-tile">
      <span className="stat-tile__label">{label}</span>
      <span className={flagged ? "stat-tile__value stat-tile__value--flagged" : "stat-tile__value"}>{text}</span>
    </div>
  );
}
