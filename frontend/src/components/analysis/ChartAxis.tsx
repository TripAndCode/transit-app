/** Shared y-axis gridlines + value labels for the focused-analysis SVG line charts. */
export function ChartAxis({ low, high, y }: { low: number; high: number; y: (value: number) => number }) {
  return <>{[0, 1, 2, 3, 4].map((tick) => {
    const value = low + (high - low) * tick / 4;
    return <g key={tick}>
      <line x1={52} x2={752} y1={y(value)} y2={y(value)} stroke="var(--border-subtle)" strokeDasharray="2 4" />
      <text x={42} y={y(value) + 4} textAnchor="end" fill="var(--text-secondary)" fontSize={12}>{value.toFixed(1)}</text>
    </g>;
  })}</>;
}
