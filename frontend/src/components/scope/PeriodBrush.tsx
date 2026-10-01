import { useId, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";
import type { ScopeSummary } from "../../api/types";
import { delayRampVar } from "../../styles/tokens";
import { calendarDays, dayIndexAt, rangeFrom } from "./brushMath";

const DAY_W = 8;
const H = 48;
const BAR_H = 44;
/** Bars saturate here so one bad day does not flatten the rest. */
const CAP_MIN = 8;

function md(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${m}/${d}`;
}

function indexOf(calendar: string[], date: string): number {
  if (date <= calendar[0]) return 0;
  if (date >= calendar[calendar.length - 1]) return calendar.length - 1;
  return calendar.indexOf(date);
}

/** Daily mean delay over the summary's window, with the period selected on
 *  it. Drag across the bars to pick a range; the two handles move a day at
 *  a time from the keyboard. Each writes once, when the drag or key ends.
 *  A day without data is hatched, never drawn as zero. */
export function PeriodBrush({
  days,
  latest,
  from,
  to,
  onCommit,
}: {
  days: ScopeSummary["days"];
  latest: string;
  from: string;
  to: string;
  onCommit: (from: string, to: string) => void;
}) {
  const { t } = useTranslation();
  const patternId = `scope-gap-${useId().replace(/:/g, "")}`;
  const [draft, setDraft] = useState<[number, number] | null>(null);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  if (days.length === 0) return null;
  const calendar = calendarDays(days[0].date, latest);
  const byDate = new Map(days.map((d) => [d.date, d]));
  const n = calendar.length;
  const [start, end] = draft ?? [indexOf(calendar, from), indexOf(calendar, to)];

  function at(e: PointerEvent<SVGSVGElement>): number {
    const box = e.currentTarget.getBoundingClientRect();
    return dayIndexAt(e.clientX - box.left, box.width, n);
  }
  function onPointerDown(e: PointerEvent<SVGSVGElement>) {
    const i = at(e);
    setDragFrom(i);
    setDraft([i, i]);
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }
  function onPointerMove(e: PointerEvent<SVGSVGElement>) {
    if (dragFrom == null) return;
    setDraft(rangeFrom(dragFrom, at(e)));
  }
  function onPointerUp(e: PointerEvent<SVGSVGElement>) {
    if (dragFrom == null) return;
    const [a, b] = rangeFrom(dragFrom, at(e));
    setDragFrom(null);
    setDraft(null);
    onCommit(calendar[a], calendar[b]);
  }
  function onHandleKeyDown(edge: "start" | "end", e: KeyboardEvent<HTMLButtonElement>) {
    const step = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
    if (step === 0) return;
    e.preventDefault();
    const next: [number, number] =
      edge === "start" ? [Math.min(end, Math.max(0, start + step)), end] : [start, Math.max(start, Math.min(n - 1, end + step))];
    if (next[0] !== start || next[1] !== end) setDraft(next);
  }
  function onHandleKeyUp() {
    if (!draft) return;
    setDraft(null);
    onCommit(calendar[draft[0]], calendar[draft[1]]);
  }

  return (
    <div className="scope-brush">
      <svg
        role="img"
        aria-label={t("scope.control.brush_label")}
        viewBox={`0 0 ${n * DAY_W} ${H}`}
        preserveAspectRatio="none"
        width="100%"
        height={H}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        <defs>
          <pattern id={patternId} width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="2" height="4" fill="var(--border-subtle)" />
          </pattern>
        </defs>
        <rect
          className="scope-brush__selection"
          x={start * DAY_W}
          y={0}
          width={(end - start + 1) * DAY_W}
          height={H}
          fill="var(--accent-soft)"
        />
        {calendar.map((date, i) => {
          const day = byDate.get(date);
          if (!day) {
            return (
              <rect key={date} className="scope-brush__gap" x={i * DAY_W + 1} y={H - BAR_H} width={DAY_W - 2} height={BAR_H} fill={`url(#${patternId})`} />
            );
          }
          const h = Math.max(2, (Math.min(day.avg_min, CAP_MIN) / CAP_MIN) * BAR_H);
          return (
            <rect
              key={date}
              className="scope-brush__bar"
              x={i * DAY_W + 1}
              y={H - h}
              width={DAY_W - 2}
              height={h}
              fill={delayRampVar(day.avg_min)}
            />
          );
        })}
      </svg>
      {(["start", "end"] as const).map((edge) => {
        const index = edge === "start" ? start : end;
        const left = ((edge === "start" ? index : index + 1) / n) * 100;
        return (
          <button
            key={edge}
            type="button"
            role="slider"
            className="scope-brush__handle"
            style={{ left: `${left}%` }}
            aria-label={t(`scope.control.brush_${edge}`)}
            aria-valuemin={0}
            aria-valuemax={n - 1}
            aria-valuenow={index}
            aria-valuetext={md(calendar[index])}
            onKeyDown={(e) => onHandleKeyDown(edge, e)}
            onKeyUp={onHandleKeyUp}
          />
        );
      })}
      <div className="scope-brush__axis">
        <span>{md(calendar[0])}</span>
        <span>{md(calendar[n - 1])}</span>
      </div>
    </div>
  );
}
