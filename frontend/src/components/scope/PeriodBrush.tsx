import { useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";
import { isoDaysBefore } from "../../api/scope";
import type { ScopeSummary } from "../../api/types";
import { delayRampVar } from "../../styles/tokens";
import { calendarDays, dayIndexAt, rangeFrom } from "./brushMath";

const DAY_W = 8;
const H = 48;
const BAR_H = 44;
const GAP_H = 4;
/** Bars saturate here so one bad day does not flatten the rest. */
const CAP_MIN = 8;
/** The summary's `days` window (pipeline/reports/scope_summary.py). */
const WINDOW_DAYS = 90;
const PAGE_DAYS = 7;
const RAMP_LEGEND = [
  ["var(--d0)", "legend_d0"],
  ["var(--d1)", "legend_d1"],
  ["var(--d2)", "legend_d2"],
  ["var(--d3)", "legend_d3"],
  ["var(--d4)", "legend_d4"],
] as const;

type Edge = "start" | "end";
type Drag = { origin: number; anchor: number; edge: Edge | null; moved: boolean };

function md(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${m}/${d}`;
}

/** A date's place in the window: its index, or -1 before it / `n` after it. */
function positionOf(calendar: string[], date: string): number {
  if (date < calendar[0]) return -1;
  if (date > calendar[calendar.length - 1]) return calendar.length;
  return calendar.indexOf(date);
}

function ordered(a: string, b: string): [string, string] {
  return a <= b ? [a, b] : [b, a];
}

/** Daily mean delay over the summary's window, with the period outlined on
 *  it. Drag across the bars to pick a range, or drag a handle to move one
 *  edge; from the keyboard each handle moves a day at a time (Home/End, Page
 *  keys for a week). A change writes once, when the drag or key ends, and
 *  only the edge that moved: an edge outside the window keeps its date. A day
 *  without data is a short mark, never drawn as zero or as a tall bar. */
export function PeriodBrush({
  days,
  earliest,
  latest,
  from,
  to,
  onCommit,
}: {
  days: ScopeSummary["days"];
  earliest: string | null;
  latest: string;
  from: string;
  to: string;
  onCommit: (from: string, to: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const titleId = useId();
  const svgRef = useRef<SVGSVGElement>(null);
  const [draft, setDraft] = useState<[number, number] | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  if (days.length === 0) return null;

  const windowFloor = isoDaysBefore(latest, WINDOW_DAYS - 1);
  const first = earliest ?? days[0].date;
  const calendar = calendarDays(first > windowFloor ? first : windowFloor, latest);
  const byDate = new Map(days.map((d) => [d.date, d]));
  const n = calendar.length;
  const fromPos = positionOf(calendar, from);
  const toPos = positionOf(calendar, to);
  const overlaps = toPos >= 0 && fromPos < n;
  const clamp = (i: number) => Math.min(n - 1, Math.max(0, i));
  const [start, end] = draft ?? [clamp(fromPos), clamp(toPos)];
  const longDate = new Intl.DateTimeFormat(i18n.language, { month: "short", day: "numeric", timeZone: "UTC" });
  const spoken = (iso: string) => longDate.format(new Date(`${iso}T00:00:00Z`));

  function dayAt(clientX: number): number {
    const box = svgRef.current?.getBoundingClientRect();
    return box ? dayIndexAt(clientX - box.left, box.width, n) : 0;
  }
  /** The period a draft or drag stands for, keeping an edge that did not move. */
  function commitEdge(edge: Edge, index: number) {
    const [a, b] = edge === "start" ? ordered(calendar[index], to) : ordered(from, calendar[index]);
    if (a !== from || b !== to) onCommit(a, b);
  }

  function onPointerDown(e: PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    const edge = ((e.target as HTMLElement).dataset?.edge as Edge | undefined) ?? null;
    const i = dayAt(e.clientX);
    const anchor = edge === "start" ? end : edge === "end" ? start : i;
    setDrag({ origin: i, anchor, edge, moved: false });
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }
  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    if (!drag) return;
    const i = dayAt(e.clientX);
    if (!drag.moved && i === drag.origin) return;
    if (!drag.moved) setDrag({ ...drag, moved: true });
    setDraft(rangeFrom(drag.anchor, i));
  }
  function onPointerUp(e: PointerEvent<HTMLDivElement>) {
    if (!drag) return;
    const moved = drag.moved;
    const i = dayAt(e.clientX);
    setDrag(null);
    setDraft(null);
    if (!moved) return;
    if (drag.edge) commitEdge(drag.edge, i);
    else {
      const [a, b] = rangeFrom(drag.anchor, i);
      onCommit(calendar[a], calendar[b]);
    }
  }
  function cancelDrag() {
    setDrag(null);
    setDraft(null);
  }

  function onHandleKeyDown(edge: Edge, e: KeyboardEvent<HTMLButtonElement>) {
    const lo = edge === "start" ? 0 : start;
    const hi = edge === "start" ? end : n - 1;
    const current = edge === "start" ? start : end;
    const target =
      e.key === "ArrowLeft" || e.key === "ArrowDown" ? current - 1
      : e.key === "ArrowRight" || e.key === "ArrowUp" ? current + 1
      : e.key === "PageDown" ? current - PAGE_DAYS
      : e.key === "PageUp" ? current + PAGE_DAYS
      : e.key === "Home" ? lo
      : e.key === "End" ? hi
      : null;
    if (target == null) return;
    e.preventDefault();
    const next = Math.min(hi, Math.max(lo, target));
    if (next !== current) setDraft(edge === "start" ? [next, end] : [start, next]);
  }
  function commitDraft(edge: Edge) {
    if (!draft) return;
    setDraft(null);
    commitEdge(edge, edge === "start" ? draft[0] : draft[1]);
  }

  return (
    <div className="scope-brush">
      <div
        className="scope-brush__track"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={cancelDrag}
        onLostPointerCapture={cancelDrag}
      >
        <svg
          ref={svgRef}
          role="img"
          aria-labelledby={titleId}
          viewBox={`0 0 ${n * DAY_W} ${H}`}
          preserveAspectRatio="none"
          width="100%"
          height={H}
        >
          <title id={titleId}>{t("scope.control.brush_label")}</title>
          {calendar.map((date, i) => {
            const day = byDate.get(date);
            if (!day) {
              return (
                <rect
                  key={date}
                  className="scope-brush__gap"
                  x={i * DAY_W + 1}
                  y={H - GAP_H}
                  width={DAY_W - 2}
                  height={GAP_H}
                  fill="var(--border-subtle)"
                >
                  <title>{t("scope.control.no_data")}</title>
                </rect>
              );
            }
            const h = Math.max(2, (Math.min(day.avg_min, CAP_MIN) / CAP_MIN) * BAR_H);
            const inside = overlaps && i >= start && i <= end;
            return (
              <rect
                key={date}
                className={inside || !overlaps ? "scope-brush__bar" : "scope-brush__bar scope-brush__bar--out"}
                x={i * DAY_W + 1}
                y={H - h}
                width={DAY_W - 2}
                height={h}
                fill={delayRampVar(day.avg_min)}
              >
                <title>{`${md(date)} · ${t("scope.control.mean_min", { n: day.avg_min.toFixed(1) })}`}</title>
              </rect>
            );
          })}
          {(overlaps || draft) && (
            <rect
              className="scope-brush__selection"
              x={start * DAY_W}
              y={0.5}
              width={(end - start + 1) * DAY_W}
              height={H - 1}
              fill="none"
              stroke="var(--accent)"
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
        {(["start", "end"] as const).map((edge) => {
          const index = edge === "start" ? start : end;
          const left = ((edge === "start" ? index : index + 1) / n) * 100;
          const date = draft ? calendar[index] : edge === "start" ? from : to;
          return (
            <button
              key={edge}
              type="button"
              role="slider"
              data-edge={edge}
              className="scope-brush__handle"
              style={{ left: `${left}%` }}
              aria-label={t(`scope.control.brush_${edge}`)}
              aria-valuemin={edge === "start" ? 0 : start}
              aria-valuemax={edge === "start" ? end : n - 1}
              aria-valuenow={index}
              aria-valuetext={spoken(date)}
              onKeyDown={(e) => onHandleKeyDown(edge, e)}
              onKeyUp={() => commitDraft(edge)}
              onBlur={() => commitDraft(edge)}
            />
          );
        })}
      </div>
      <div className="scope-brush__axis">
        <span>{md(calendar[0])}</span>
        <span>{md(calendar[n - 1])}</span>
      </div>
      <div className="scope-legend">
        {RAMP_LEGEND.map(([fill, key]) => (
          <span key={key}>
            <i style={{ background: fill }} aria-hidden="true" />
            {t(`scope.control.${key}`)}
          </span>
        ))}
        <span>
          <i className="scope-legend__gap" aria-hidden="true" />
          {t("scope.control.no_data")}
        </span>
      </div>
    </div>
  );
}
