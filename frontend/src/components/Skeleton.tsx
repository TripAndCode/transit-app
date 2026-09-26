type Props = {
  width?: number | string;
  height?: number | string;
  style?: React.CSSProperties;
};

export function Skeleton({ width = "100%", height = 16, style }: Props) {
  return <div className="skeleton" style={{ width, height, ...style }} />;
}

/* The composites below exist so a loading screen has the shape of the screen
   it is standing in for. One tall grey slab tells the reader nothing about
   what is coming, and the real content then lands in a different layout than
   the placeholder reserved; a placeholder with the same geometry settles in
   place instead.

   All of them are aria-hidden: a skeleton carries no information, and
   announcing the wait is the job of whatever owns the request. */

/** A row of headline metric tiles: a short caption bar over a tall value bar. */
export function SkeletonKpiRow({ tiles = 3 }: { tiles?: number }) {
  return (
    <div aria-hidden="true" style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
      {Array.from({ length: tiles }, (_, i) => (
        <div
          key={i}
          data-testid="skeleton-kpi-tile"
          style={{
            flex: "1 1 180px",
            display: "flex",
            flexDirection: "column",
            gap: 10,
            padding: 16,
            border: "1px solid var(--card-border)",
            borderRadius: "var(--card-radius)",
          }}
        >
          <Skeleton height={12} width="45%" />
          <Skeleton height={28} width="70%" />
        </div>
      ))}
    </div>
  );
}

/** A caption bar over a plot area of the chart's real height. */
export function SkeletonChart({ height = 240 }: { height?: number }) {
  return (
    <div aria-hidden="true" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Skeleton height={14} width="30%" />
      <div data-testid="skeleton-chart-plot" style={{ height }}>
        <Skeleton height="100%" />
      </div>
    </div>
  );
}

/** A stack of equal rows, one per row the list is expected to return. */
export function SkeletonTable({ rows = 6, rowHeight = 40 }: { rows?: number; rowHeight?: number }) {
  return (
    <div aria-hidden="true" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} data-testid="skeleton-table-row" style={{ height: rowHeight }}>
          <Skeleton height="100%" />
        </div>
      ))}
    </div>
  );
}
