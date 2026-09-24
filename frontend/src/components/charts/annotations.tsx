/**
 * Chart annotations: marks that say something *about* the data rather than
 * plotting it. They are deliberately quiet — a threshold is an outline and a
 * hairline tint, never a fill that competes with the series — and they take
 * plain SVG user units so a hand-rolled chart can drop them into its own
 * coordinate space without a scale abstraction.
 */

type ThresholdBandProps = {
  /** Value-domain lower edge of the band (the threshold itself). */
  min: number;
  /** Value-domain upper edge — normally the top of the plotted axis. */
  max: number;
  /** The chart's value → y mapping, clamped to the plot by the caller. */
  toY: (value: number) => number;
  x: number;
  width: number;
  label?: string;
};

/**
 * The region beyond a threshold. Rendered as a faint tint plus a dashed rule
 * on the threshold line: the severity is an annotation, so the series keeps
 * its own encoding instead of a value's colour changing when it crosses.
 * Renders nothing when the band collapses — a threshold above everything
 * plotted has nothing to say.
 */
export function ThresholdBand({ min, max, toY, x, width, label }: ThresholdBandProps) {
  const yMin = toY(min);
  const yMax = toY(max);
  const top = Math.min(yMin, yMax);
  const bottom = Math.max(yMin, yMax);
  if (!(bottom > top)) return null;
  return (
    <g data-testid="threshold-band" pointerEvents="none">
      <rect x={x} y={top} width={width} height={bottom - top} fill="var(--delay-severe)" opacity={0.05} />
      <line
        x1={x}
        x2={x + width}
        y1={yMin}
        y2={yMin}
        stroke="var(--delay-severe)"
        strokeWidth="1"
        strokeDasharray="4 4"
        opacity={0.5}
      />
      {label && (
        <text x={x + width - 4} y={yMin - 4} textAnchor="end" fontSize="9" fill="var(--text-tertiary)">
          {label}
        </text>
      )}
    </g>
  );
}

type VerticalMarkerProps = {
  x: number;
  y1: number;
  y2: number;
  label?: string;
  /** Tooltip text for pointer users; the enclosing chart's `aria-label`
   *  carries the accessible description. */
  title?: string;
  /** `context` is background information the reader should be able to ignore
   *  (a schedule revision); `highlight` is a mark the chart is pointing at. */
  variant?: "context" | "highlight";
};

/** A full-height rule at one x position, optionally labelled. */
export function VerticalMarker({ x, y1, y2, label, title, variant = "context" }: VerticalMarkerProps) {
  const highlight = variant === "highlight";
  const stroke = highlight ? "var(--accent-strong)" : "var(--text-tertiary)";
  return (
    <g data-testid="vertical-marker" data-variant={variant} pointerEvents="none">
      <line
        x1={x}
        x2={x}
        y1={y1}
        y2={y2}
        stroke={stroke}
        strokeWidth="1"
        strokeDasharray={highlight ? "3 3" : "1 3"}
        opacity={highlight ? 0.85 : 0.7}
      >
        {title && <title>{title}</title>}
      </line>
      {label && (
        // A highlight labels from the baseline so it can't collide with the
        // context markers, which all label from the top.
        <text x={x + 3} y={highlight ? y2 - 3 : y1 + 9} fontSize="9" fill={stroke}>
          {label}
        </text>
      )}
    </g>
  );
}

type ShadedDaysProps = {
  /** Indices into the chart's day series. Order and duplicates don't matter. */
  days: number[];
  /** Left edge of the band belonging to a day index. */
  toX: (index: number) => number;
  bandWidth: number;
  y: number;
  height: number;
};

/** A wash over a set of days — a brush selection, or any other "these days
 *  are special" set. Consecutive days merge into one rect so a contiguous
 *  span reads as a single region instead of a row of abutting tiles. */
export function ShadedDays({ days, toX, bandWidth, y, height }: ShadedDaysProps) {
  const runs = mergeRuns(days);
  if (runs.length === 0) return null;
  return (
    <g data-testid="shaded-days" pointerEvents="none">
      {runs.map(([start, end]) => (
        <rect
          key={start}
          x={toX(start)}
          y={y}
          width={bandWidth * (end - start + 1)}
          height={height}
          fill="var(--accent)"
          opacity={0.1}
        />
      ))}
    </g>
  );
}

/** Consecutive indices collapsed into inclusive `[start, end]` pairs. */
function mergeRuns(indices: number[]): [number, number][] {
  const sorted = Array.from(new Set(indices)).sort((a, b) => a - b);
  const runs: [number, number][] = [];
  for (const i of sorted) {
    const last = runs[runs.length - 1];
    if (last && i === last[1] + 1) last[1] = i;
    else runs.push([i, i]);
  }
  return runs;
}
