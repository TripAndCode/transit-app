import type { AgencyClampDay } from "../../api/admin";

/** Clamp-rate trend for the recent window, as a bare polyline.
 *
 *  A day with no observations is a gap in the line, not a point at zero:
 *  "nothing was observed" and "nothing was implausible" are different
 *  findings, and drawing the first as the second reads as a healthy feed.
 *
 *  The vertical scale is the window's own maximum (floored at a small
 *  non-zero value) so a healthy agency's rounding noise doesn't render as a
 *  dramatic mountain range.
 */
export function ClampSparkline({ days, label }: { days: AgencyClampDay[]; label: string }) {
  const values = days.map((d) => d.clamp_pct);
  const observed = values.filter((v): v is number => v != null);
  if (observed.length === 0) {
    return (
      <span style={{ color: "var(--text-tertiary)", fontSize: 12 }} title={label}>
        —
      </span>
    );
  }
  const width = 70;
  const height = 18;
  const max = Math.max(...observed, 0.5);
  const step = values.length > 1 ? width / (values.length - 1) : 0;

  // One <polyline> per unbroken run of observed days, so a gap stays a gap.
  const segments: string[][] = [];
  let run: string[] = [];
  values.forEach((v, i) => {
    if (v == null) {
      if (run.length) segments.push(run);
      run = [];
      return;
    }
    run.push(`${(i * step).toFixed(1)},${(height - (v / max) * (height - 2) - 1).toFixed(1)}`);
  });
  if (run.length) segments.push(run);

  const latest = observed[observed.length - 1];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <svg width={width} height={height} role="img" aria-label={label} style={{ display: "block" }}>
        {segments.map((points) => (
          <polyline
            key={points[0]}
            points={points.join(" ")}
            fill="none"
            stroke="var(--accent)"
            strokeWidth={1.5}
            strokeLinejoin="round"
          />
        ))}
      </svg>
      <span style={{ fontSize: 11, color: "var(--text-tertiary)", fontVariantNumeric: "tabular-nums" }}>
        {latest.toFixed(2)}%
      </span>
    </span>
  );
}
