import "./StatTile.css";

/** Small bordered stat card: a label over a large number. `flagged` colors
 *  the value --delay-flag instead of --accent-strong for "needs attention"
 *  counts (e.g. a nonzero 5-minutes-plus-delay count) -- callers decide
 *  what counts as flagged, this component only renders the two states. */
export function StatTile({ label, value, flagged }: { label: string; value: string; flagged?: boolean }) {
  return (
    <div className="stat-tile">
      <span className="stat-tile__label">{label}</span>
      <span className={flagged ? "stat-tile__value stat-tile__value--flagged" : "stat-tile__value"}>{value}</span>
    </div>
  );
}
