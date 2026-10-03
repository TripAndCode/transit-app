import { useRef, type CSSProperties } from "react";
import { useDrawOn } from "./charts/ChartEnter";

type Props = {
  points: number[];
  width?: number;
  height?: number;
  accent?: string;
  /** When true, always render in `accent`; skip the auto green-on-improvement. */
  forceAccent?: boolean;
  showEndDot?: boolean;
  showLabels?: boolean;
  style?: CSSProperties;
  /** Draws a dashed horizontal reference line at this value, in the series'
   *  own units. The y-scale spans the series min..max, which on its own says
   *  nothing about how large the swing actually is -- a reference the reader
   *  already understands (the period mean, a target, zero) is what turns the
   *  shape back into a reading. No SVG text comes with it: callers stretch
   *  this chart with `preserveAspectRatio="none"`, which would distort any
   *  glyph, so the label belongs in the caller's own HTML. */
  baseline?: number;
  /** Passed straight to the `<svg>`. Set to `"none"` when the element is
   *  stretched via CSS (e.g. `position: absolute; inset: 0`) to fill a box
   *  whose aspect ratio doesn't match `width`/`height` -- a full-bleed
   *  background sparkline -- so it fills edge-to-edge instead of
   *  letterboxing under the default `xMidYMid meet`. */
  preserveAspectRatio?: string;
};

export function InlineSparkline({
  points,
  width = 160,
  height = 48,
  accent = "var(--trend-bad)",
  forceAccent = false,
  showEndDot = true,
  showLabels = true,
  style,
  baseline,
  preserveAspectRatio,
}: Props) {
  // Called unconditionally, ahead of the early return below -- hooks can't
  // themselves be conditional on `points.length`.
  const lineRef = useRef<SVGPolylineElement | null>(null);
  useDrawOn(lineRef);

  if (!points || points.length < 2) {
    return null;
  }
  // Trend down (last <= first) = improvement => muted green (unless forced).
  const first = points[0];
  const last_v = points[points.length - 1];
  const stroke = forceAccent ? accent : last_v <= first ? "var(--trend-good)" : accent;

  const y_min = Math.min(...points);
  const y_max = Math.max(...points);
  const span = y_max - y_min || 1;

  const stepX = width / (points.length - 1);
  const top_pad = showLabels ? 12 : 2;
  const bottom_pad = 2;
  const usable_h = height - top_pad - bottom_pad;
  const toY = (v: number) =>
    height - bottom_pad - ((v - y_min) / span) * usable_h;

  const coords = points.map((v, i) => {
    const x = i * stepX;
    const y = toY(v);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = coords[coords.length - 1].split(",");
  const lastX = parseFloat(last[0]);
  const lastY = parseFloat(last[1]);
  const firstCoord = coords[0].split(",");
  const firstY = parseFloat(firstCoord[1]);

  // Area path: polyline + close back along the bottom.
  const area_path =
    `M 0,${firstY.toFixed(1)} ` +
    coords
      .slice(1)
      .map((c) => {
        const [cx, cy] = c.split(",");
        return `L ${cx},${cy}`;
      })
      .join(" ") +
    ` L ${lastX.toFixed(1)},${height} L 0,${height} Z`;

  const baselineY = baseline == null ? null : toY(baseline);

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio={preserveAspectRatio}
      style={{ display: "inline-block", verticalAlign: "-2px", ...style }}
      role="img"
      aria-hidden
    >
      <path d={area_path} style={{ fill: stroke, fillOpacity: 0.12 }} stroke="none" />
      {baselineY != null && (
        <line
          data-testid="sparkline-baseline"
          x1={0}
          x2={width}
          y1={baselineY}
          y2={baselineY}
          strokeWidth="1"
          strokeDasharray="3 3"
          style={{ stroke: "var(--text-tertiary)", opacity: 0.6 }}
        />
      )}
      <polyline
        ref={lineRef}
        fill="none"
        style={{ stroke }}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={coords.join(" ")}
      />
      {showEndDot && <circle cx={lastX} cy={lastY} r="2.5" style={{ fill: stroke }} />}
      {showLabels && (
        <>
          <text
            x={2}
            y={10}
            fontSize="10"
            style={{ fill: "var(--text-secondary)" }}
            textAnchor="start"
          >
            {first.toFixed(1)}
          </text>
          <text
            x={width - 2}
            y={10}
            fontSize="10"
            style={{ fill: "var(--text-secondary)" }}
            textAnchor="end"
          >
            {last_v.toFixed(1)}
          </text>
        </>
      )}
    </svg>
  );
}
