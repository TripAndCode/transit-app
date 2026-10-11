import { useTranslation } from "react-i18next";
import { segmentColor, type StopRibbonSegment } from "./mareyLayout";

const RIBBON_WIDTH = 780;

/** Horizontal stop sequence, each band filled by that stop's delay.
 *
 *  The compact companion to the Marey diagram: same stops, same ramp, one
 *  line of vertical space. A read-only strip: it adds no tab stops, and its
 *  svg is a single image whose per-stop values are in the band titles.
 */
export function StopRibbon({
  segments,
  label,
  height = 18,
  markers,
}: {
  segments: StopRibbonSegment[];
  /** Already-translated accessible name. Pass `t(...)`, never a key. */
  label: string;
  height?: number;
  /** Moving positions drawn over the bands, as a fraction of the axis: 0 is
   *  the first stop's band centre, 1 the last's. */
  markers?: { fraction: number; color: string; key: string }[];
}) {
  const { t } = useTranslation("design");
  if (!segments.length) return null;
  const bandWidth = RIBBON_WIDTH / segments.length;
  return (
    <svg className="stop-ribbon" viewBox={`0 0 ${RIBBON_WIDTH} ${height + 2}`} role="img" aria-label={label}>
      {segments.map((segment, index) => (
        <rect
          key={segment.stop_sequence}
          data-stop-sequence={segment.stop_sequence}
          x={index * bandWidth + 1}
          y={1}
          width={Math.max(1, bandWidth - 2)}
          height={height}
          rx={2}
          fill={segment.delay_sec == null ? "var(--track-bg)" : segmentColor(segment.delay_sec)}
        >
          <title>
            {segment.delay_sec == null
              ? `${segment.stop_name}: ${t("missing")}`
              : `${segment.stop_name}: ${(segment.delay_sec / 60).toFixed(1)} ${t("minutes")}`}
          </title>
        </rect>
      ))}
      {markers?.map((m) => (
        <circle key={m.key} className="stop-ribbon__pos" cx={0}
          style={{ transform: `translateX(${(m.fraction * (segments.length - 1) + 0.5) * bandWidth}px)` }} cy={1 + height / 2} r={7} fill={m.color} />
      ))}
    </svg>
  );
}
