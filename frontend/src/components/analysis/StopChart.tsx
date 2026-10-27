import { useRef } from "react";
import { useTranslation } from "react-i18next";
import type { RouteShapeStop } from "../../api/types";
import { matchedPrevious } from "./stopSeries";
import { DELAY_RAMP, DELAY_THRESHOLDS, delayColor } from "../../styles/tokens";
import { ChartAxis } from "./ChartAxis";
import { showsLabel } from "./axisLabels";
import { useDrawOn } from "../charts/ChartEnter";
import { niceAxis } from "../charts/niceAxis";

/** What the stop dots' colours mean, read from the bands `delayColor`
 *  paints, so the key cannot drift from the dots. */
export function DelayBandLegend() {
  const { t } = useTranslation("design");
  const { mild, moderate, severe } = DELAY_THRESHOLDS;
  const bands = [
    { color: DELAY_RAMP.ok, label: t("legendBelow", { max: mild }) },
    { color: DELAY_RAMP.mild, label: t("legendBetween", { min: mild, max: moderate }) },
    { color: DELAY_RAMP.moderate, label: t("legendBetween", { min: moderate, max: severe }) },
    { color: DELAY_RAMP.severe, label: t("legendAbove", { min: severe }) },
  ];
  return <>{bands.map((b) => <span key={b.label}><span aria-hidden="true" style={{ color: b.color }}>●</span> {b.label}</span>)}</>;
}

/** `highlight` names a segment by its two stops' sequences, drawn as a
 *  wide band under the line. A stop with no departure observed reads as
 *  missing, except the last, from which nothing departs. */
export function StopChart({ stops, previous, selected, onSelect, highlight = null }: {
  stops: RouteShapeStop[]; previous: RouteShapeStop[]; selected: number; onSelect: (sequence: number) => void;
  highlight?: { from: number; to: number } | null;
}) {
  const { t } = useTranslation("design");
  // Only the current-period line draws on. The dashed "previous period" line
  // keeps a static `strokeDasharray: "5 5"` -- useDrawOn overrides
  // stroke-dasharray to reveal the path, which would flatten that pattern
  // into one long dash for the length of the animation and leave it looking
  // like a plain solid line forever after.
  const lineRef = useRef<SVGPathElement | null>(null);
  useDrawOn(lineRef, stops.length > 0);
  const low = Math.min(0, ...stops.map((s) => s.avg_min ?? 0), ...previous.map((s) => s.avg_min ?? 0));
  const high = Math.max(1, ...stops.map((s) => s.avg_min ?? 0), ...previous.map((s) => s.avg_min ?? 0));
  const axis = niceAxis(low, high, 6);
  const x = (i: number) => 52 + i / Math.max(1, stops.length - 1) * 700;
  const y = (n: number) => 280 - (n - axis.low) / (axis.high - axis.low) * 240;
  function path(values: (number | null)[]) {
    return values.map((v, i) => v == null ? "" : `${i === 0 || values[i - 1] == null ? "M" : "L"}${x(i)},${y(v)}`).join(" ");
  }
  const band = highlight ? [highlight.from, highlight.to].map((seq) => stops.findIndex((s) => s.stop_sequence === seq)) : [];
  const [bandFrom, bandTo] = band;
  const bandShown = band.length === 2 && bandFrom >= 0 && bandTo >= 0
    && stops[bandFrom].avg_min != null && stops[bandTo].avg_min != null;
  const last = stops.length - 1;
  const missingLabel = (i: number) => t(i === last ? "terminus" : "missing");
  return <svg className="focus-chart" viewBox="0 0 780 335" role="group" aria-label={t("stopDelay")}>
    <ChartAxis ticks={axis.ticks} y={y} />
    {bandShown && <line data-testid="stop-chart-highlight" x1={x(bandFrom)} y1={y(stops[bandFrom].avg_min!)} x2={x(bandTo)} y2={y(stops[bandTo].avg_min!)}
      stroke="var(--accent)" strokeOpacity={0.22} strokeWidth={14} strokeLinecap="round" />}
    <path d={path(stops.map((s) => matchedPrevious(s, previous)))} fill="none" stroke="var(--text-secondary)" strokeWidth={2} strokeDasharray="5 5" />
    <path ref={lineRef} d={path(stops.map((s) => s.avg_min))} fill="none" stroke="var(--accent)" strokeWidth={2.5} />
    {stops.map((s, i) => <g key={`${s.stop_sequence}-${s.stop_id}`}>
      <circle cx={x(i)} cy={s.avg_min == null ? 280 : y(s.avg_min)} r={s.stop_sequence === selected ? 7 : 4}
        fill={s.avg_min == null ? "var(--bg-surface)" : delayColor(s.avg_min)} stroke="var(--text-secondary)"
        className="svg-focus-ring"
        role="button" tabIndex={0} aria-label={`${s.stop_name}: ${s.avg_min == null ? missingLabel(i) : `${s.avg_min} ${t("minutes")}`}`}
        onClick={() => onSelect(s.stop_sequence)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(s.stop_sequence); } }}>
        <title>{s.stop_name}</title>
      </circle>
      {showsLabel(i, stops.length) &&
        <text x={x(i)} y={307} textAnchor="middle" fill="var(--text-secondary)" fontSize={14}>{s.stop_name.slice(0, 7)}</text>}
    </g>)}
  </svg>;
}
