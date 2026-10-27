import { useRef } from "react";
import { useTranslation } from "react-i18next";
import type { RouteShapeStop } from "../../api/types";
import { matchedPrevious } from "./stopSeries";
import { missingStopKey } from "./routeDossier";
import { DELAY_RAMP, DELAY_THRESHOLDS, delayColor } from "../../styles/tokens";
import { ChartAxis } from "./ChartAxis";
import { showsLabel } from "./axisLabels";
import { useDrawOn } from "../charts/ChartEnter";
import { fitLabel, useSvgTextMeasurer } from "../charts/labelFit";
import { niceAxis } from "../charts/niceAxis";

/** The drawing's size when no width is given; the page then scales it to
 *  its box (focusedAnalysis.css keeps that box wide enough for the labels). */
const VIEW_WIDTH = 780;
const VIEW_HEIGHT = 335;
/** Where the plot starts (past the value labels) and how far it stops short
 *  of the right edge. */
const PLOT_LEFT = 52;
const PLOT_RIGHT = 28;
const LABEL_SIZE = 14;
/** Clear space kept between two neighbouring stop names, and between a name
 *  and the chart's edge. */
const LABEL_GAP = 8;
/** The closest two stop names may sit, centre to centre, in a drawing given
 *  its width: about four CJK characters and the gap. */
const MIN_LABEL_ROOM = 64;
/** How many stop names the full-width drawing aims for. */
const LABEL_TARGET = 7;

/** Each shown stop name in the room between it and its neighbours: halfway
 *  to the next shown stop on either side, out to the chart's edge for the
 *  first and last. A name wider than its room ends in an ellipsis, and one
 *  near an edge slides inward rather than past it. Without a measurer every
 *  name is drawn whole at its stop. */
function stopLabels(names: string[], xs: number[], shown: number[], viewWidth: number, measure: ((text: string) => number) | null) {
  return shown.map((i, k) => {
    const left = k === 0 ? LABEL_GAP / 2 : (xs[shown[k - 1]] + xs[i]) / 2 + LABEL_GAP / 2;
    const right = k === shown.length - 1 ? viewWidth - LABEL_GAP / 2 : (xs[i] + xs[shown[k + 1]]) / 2 - LABEL_GAP / 2;
    if (!measure) return { index: i, text: names[i], x: xs[i] };
    const text = fitLabel(names[i], right - left, measure);
    const half = measure(text) / 2;
    return { index: i, text, x: Math.min(Math.max(xs[i], left + half), right - half) };
  });
}

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
 *  missing, except the last, from which nothing departs. Given a `width` in
 *  pixels the chart draws at exactly that size, one viewBox unit per pixel,
 *  so a narrow column gets a narrower plot with fewer stop names rather than
 *  a shrunken one; without it the full-width drawing scales to its box. */
export function StopChart({ stops, previous, selected, onSelect, highlight = null, width }: {
  stops: RouteShapeStop[]; previous: RouteShapeStop[]; selected: number; onSelect: (sequence: number) => void;
  highlight?: { from: number; to: number } | null; width?: number;
}) {
  const { t } = useTranslation("design");
  // Only the current-period line draws on. The dashed "previous period" line
  // keeps a static `strokeDasharray: "5 5"` -- useDrawOn overrides
  // stroke-dasharray to reveal the path, which would flatten that pattern
  // into one long dash for the length of the animation and leave it looking
  // like a plain solid line forever after.
  const lineRef = useRef<SVGPathElement | null>(null);
  useDrawOn(lineRef, stops.length > 0);
  const measure = useSvgTextMeasurer(LABEL_SIZE);
  const low = Math.min(0, ...stops.map((s) => s.avg_min ?? 0), ...previous.map((s) => s.avg_min ?? 0));
  const high = Math.max(1, ...stops.map((s) => s.avg_min ?? 0), ...previous.map((s) => s.avg_min ?? 0));
  const axis = niceAxis(low, high, 6);
  const viewWidth = width ?? VIEW_WIDTH;
  const plotWidth = viewWidth - PLOT_LEFT - PLOT_RIGHT;
  const x = (i: number) => PLOT_LEFT + i / Math.max(1, stops.length - 1) * plotWidth;
  const target = width == null ? LABEL_TARGET : Math.max(2, Math.min(LABEL_TARGET, Math.floor(plotWidth / MIN_LABEL_ROOM)));
  const labels = stopLabels(
    stops.map((s) => s.stop_name),
    stops.map((_, i) => x(i)),
    stops.flatMap((_, i) => (showsLabel(i, stops.length, target) ? [i] : [])),
    viewWidth,
    measure,
  );
  const y = (n: number) => 280 - (n - axis.low) / (axis.high - axis.low) * 240;
  function path(values: (number | null)[]) {
    return values.map((v, i) => v == null ? "" : `${i === 0 || values[i - 1] == null ? "M" : "L"}${x(i)},${y(v)}`).join(" ");
  }
  const band = highlight ? [highlight.from, highlight.to].map((seq) => stops.findIndex((s) => s.stop_sequence === seq)) : [];
  const [bandFrom, bandTo] = band;
  const bandShown = band.length === 2 && bandFrom >= 0 && bandTo >= 0
    && stops[bandFrom].avg_min != null && stops[bandTo].avg_min != null;
  return <svg className="focus-chart" viewBox={`0 0 ${viewWidth} ${VIEW_HEIGHT}`} role="group" aria-label={t("stopDelay")}
    style={width == null ? undefined : { width, height: VIEW_HEIGHT, minWidth: 0 }}>
    <ChartAxis ticks={axis.ticks} y={y} right={viewWidth - PLOT_RIGHT} />
    {bandShown && <line data-testid="stop-chart-highlight" x1={x(bandFrom)} y1={y(stops[bandFrom].avg_min!)} x2={x(bandTo)} y2={y(stops[bandTo].avg_min!)}
      stroke="var(--accent)" strokeOpacity={0.22} strokeWidth={14} strokeLinecap="round" />}
    <path d={path(stops.map((s) => matchedPrevious(s, previous)))} fill="none" stroke="var(--text-secondary)" strokeWidth={2} strokeDasharray="5 5" />
    <path ref={lineRef} d={path(stops.map((s) => s.avg_min))} fill="none" stroke="var(--accent)" strokeWidth={2.5} />
    {stops.map((s, i) => <g key={`${s.stop_sequence}-${s.stop_id}`}>
      <circle cx={x(i)} cy={s.avg_min == null ? 280 : y(s.avg_min)} r={s.stop_sequence === selected ? 7 : 4}
        fill={s.avg_min == null ? "var(--bg-surface)" : delayColor(s.avg_min)} stroke="var(--text-secondary)"
        className="svg-focus-ring"
        role="button" tabIndex={0} aria-label={`${s.stop_name}: ${s.avg_min == null ? t(missingStopKey(s, stops)) : `${s.avg_min} ${t("minutes")}`}`}
        onClick={() => onSelect(s.stop_sequence)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(s.stop_sequence); } }}>
        <title>{s.stop_name}</title>
      </circle>
    </g>)}
    {labels.map((label) =>
      <text key={label.index} x={label.x} y={307} textAnchor="middle" fill="var(--text-secondary)" fontSize={LABEL_SIZE}>{label.text}</text>)}
  </svg>;
}
