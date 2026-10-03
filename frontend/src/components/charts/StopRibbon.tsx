import { useTranslation } from "react-i18next";
import { segmentColor, type StopRibbonSegment } from "./mareyLayout";

const RIBBON_WIDTH = 780;

/** Horizontal stop sequence, each band filled by that stop's delay.
 *
 *  The compact companion to the Marey diagram: same stops, same ramp, one
 *  line of vertical space. Interactive only when `onSelect` is given —
 *  otherwise it is a read-only strip and adds no tab stops.
 */
export function StopRibbon({
  segments,
  label,
  selectedSequence = null,
  onSelect,
  height = 18,
}: {
  segments: StopRibbonSegment[];
  /** Already-translated accessible name. Pass `t(...)`, never a key. */
  label: string;
  selectedSequence?: number | null;
  onSelect?: (stopSequence: number) => void;
  height?: number;
}) {
  const { t } = useTranslation("design");
  const select = onSelect;
  if (!segments.length) return null;
  const bandWidth = RIBBON_WIDTH / segments.length;
  return (
    <svg className="stop-ribbon" viewBox={`0 0 ${RIBBON_WIDTH} ${height + 2}`} role="img" aria-label={label}>
      {segments.map((segment, index) => {
        const selected = segment.stop_sequence === selectedSequence;
        const interaction = select
          ? {
              role: "button",
              tabIndex: 0,
              onClick: () => select(segment.stop_sequence),
              onKeyDown: (e: React.KeyboardEvent<SVGRectElement>) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  select(segment.stop_sequence);
                }
              },
            }
          : {};
        return (
          <rect
            key={segment.stop_sequence}
            data-stop-sequence={segment.stop_sequence}
            data-selected={String(selected)}
            x={index * bandWidth + 1}
            y={1}
            width={Math.max(1, bandWidth - 2)}
            height={height}
            rx={2}
            fill={segment.delay_sec == null ? "var(--track-bg)" : segmentColor(segment.delay_sec)}
            stroke={selected ? "var(--text-primary)" : "none"}
            {...interaction}
          >
            <title>
              {segment.delay_sec == null
                ? `${segment.stop_name}: ${t("missing")}`
                : `${segment.stop_name}: ${(segment.delay_sec / 60).toFixed(1)} ${t("minutes")}`}
            </title>
          </rect>
        );
      })}
    </svg>
  );
}
