import "./LegendChip.css";

/** Colored-dot + label pill, replacing the app's old dark floating legend
 *  box pattern. */
export function LegendChip({ color, label }: { color: string; label: string }) {
  return (
    <span className="legend-chip">
      <span className="legend-chip__dot" style={{ background: color }} />
      {label}
    </span>
  );
}
