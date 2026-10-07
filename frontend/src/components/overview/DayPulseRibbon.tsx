import { useId } from "react";
import { RIBBON_H, RIBBON_W, pulseGradientStops, pulsePaths } from "./dayPulsePaths";

/** The day's delay profile as a quiet area behind the hero figure: 24 hours,
 *  ramp-coloured, 16% fill. Decoration for a reading the sentence and the
 *  figure already give in words and digits, so it is hidden from assistive
 *  tech and takes no pointer. */
export function DayPulseRibbon({ byHour }: { byHour: readonly (number | null)[] }) {
  const gradientId = `ov-pulse-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const { area, line } = pulsePaths(byHour);
  if (!area) return null;
  return (
    <svg
      className="ov-pulse-ribbon"
      viewBox={`0 0 ${RIBBON_W} ${RIBBON_H}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        {/* In user space, so the area and a line that starts late or runs
            flat share one horizontal axis; a bounding-box gradient would
            stretch over the line's own extent and drop a zero-height one. */}
        <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1={0} x2={RIBBON_W} y1={0} y2={0}>
          {pulseGradientStops(byHour).map((s) => (
            <stop key={s.offset} offset={s.offset} stopColor={s.color} />
          ))}
        </linearGradient>
      </defs>
      <path className="ov-pulse-ribbon__area" d={area} fill={`url(#${gradientId})`} opacity="0.16" />
      <path
        className="ov-pulse-ribbon__line"
        d={line}
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
        opacity="0.7"
      />
    </svg>
  );
}
