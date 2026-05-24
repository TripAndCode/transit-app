// frontend/src/components/InlineSparkline.tsx
import type { CSSProperties } from "react";

type Props = {
  points: number[];
  width?: number;
  height?: number;
  accent?: string;
  showEndDot?: boolean;
  style?: CSSProperties;
};

export function InlineSparkline({
  points,
  width = 90,
  height = 22,
  accent = "#b45309",
  showEndDot = true,
  style,
}: Props) {
  if (!points || points.length < 2) {
    return null;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const stepX = width / (points.length - 1);
  const coords = points.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / span) * (height - 4) - 2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = coords[coords.length - 1].split(",");

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
      <polyline
        fill="none"
        stroke="#8e8e93"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={coords.join(" ")}
      />
      {showEndDot && (
        <circle cx={last[0]} cy={last[1]} r="2.5" fill={accent} />
      )}
    </svg>
  );
}
