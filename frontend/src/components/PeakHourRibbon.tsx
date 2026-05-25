import { useTranslation } from "react-i18next";

import type { OverviewPeakHour } from "../api/types";

type Props = { peak_hour: OverviewPeakHour | null };

const W = 660;
const H = 80;
const PAD_BOTTOM = 18;
const cell_w = W / 24;

export function PeakHourRibbon({ peak_hour }: Props) {
  const { t } = useTranslation();
  if (peak_hour == null) return null;
  return (
    <div>
      <div className="ov-label">{t("overview.section_peak_hour")}</div>
      <svg
        width="100%"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ display: "block", maxWidth: "100%" }}
        role="img"
        aria-label={t("overview.section_peak_hour")}
      >
        {peak_hour.by_hour.map((v, h) => {
          if (v == null) return null;
          const denom = peak_hour.peak_avg_min || 1;
          const bar_h = Math.max((v / denom) * (H - PAD_BOTTOM - 2), 0);
          const x = h * cell_w + 1;
          const y = H - PAD_BOTTOM - bar_h;
          const isPeak = h === peak_hour.peak_hour;
          return (
            <rect
              key={h}
              x={x}
              y={y}
              width={cell_w - 2}
              height={bar_h}
              fill="#b45309"
              opacity={isPeak ? 0.9 : 0.35}
              rx={1}
            />
          );
        })}
        <line
          x1={0}
          y1={H - PAD_BOTTOM}
          x2={W}
          y2={H - PAD_BOTTOM}
          stroke="#e0e0e0"
          strokeWidth="1"
        />
        {[0, 6, 12, 18].map((h) => (
          <text
            key={h}
            x={h * cell_w + cell_w / 2}
            y={H - 4}
            fontSize="10"
            fill="#8e8e93"
            textAnchor="middle"
          >
            {h}
          </text>
        ))}
      </svg>
      <p className="ov-conc-legend">
        {t("overview.peak_hour_callout", {
          hour: peak_hour.peak_hour,
          next_hour: peak_hour.peak_hour + 1,
          avg: peak_hour.peak_avg_min.toFixed(1),
        })}
      </p>
    </div>
  );
}
