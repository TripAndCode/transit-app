import { fixedTenth } from "../../utils/format";
/** Shared y-axis gridlines + value labels for the focused-analysis SVG line charts. */
export function ChartAxis({ ticks, y, right = 752 }: { ticks: number[]; y: (value: number) => number; right?: number }) {
  return <>{ticks.map((value) => {
    return <g key={value}>
      <line x1={52} x2={right} y1={y(value)} y2={y(value)} stroke="var(--border-subtle)" strokeDasharray="2 4" />
      <text x={42} y={y(value) + 4} textAnchor="end" fill="var(--text-secondary)" fontSize={14}>{fixedTenth(value)}</text>
    </g>;
  })}</>;
}
