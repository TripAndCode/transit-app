// frontend/src/components/InlineSparkline.tsx
import type { CSSProperties } from "react";

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
};

export function InlineSparkline({
  points,
  width = 160,
  height = 48,
  accent = "#b45309",
  forceAccent = false,
  showEndDot = true,
  showLabels = true,
  style,
}: Props) {
  if (!points || points.length < 2) {
    return null;
  }
  // Trend down (last <= first) = improvement => muted green (unless forced).
  const first = points[0];
  const last_v = points[points.length - 1];
  const stroke = forceAccent ? accent : last_v <= first ? "#166534" : accent;

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

  return (
    <svg
      className="ov-sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: "inline-block", verticalAlign: "-2px", ...style }}
      role="img"
      aria-hidden
    >
      <path d={area_path} fill={stroke} fillOpacity={0.12} stroke="none" />
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={coords.join(" ")}
      />
      {showEndDot && <circle cx={lastX} cy={lastY} r="2.5" fill={stroke} />}
      {showLabels && (
        <>
          <text
            x={2}
            y={10}
            fontSize="10"
            fill="#6e6e73"
            textAnchor="start"
          >
            {first.toFixed(1)}
          </text>
          <text
            x={width - 2}
            y={10}
            fontSize="10"
            fill="#6e6e73"
            textAnchor="end"
          >
            {last_v.toFixed(1)}
          </text>
        </>
      )}
    </svg>
  );
}
