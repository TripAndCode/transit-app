import { useTranslation } from "react-i18next";

import type { OverviewPeakHour } from "../api/types";

type Props = { peak_hour: OverviewPeakHour | null };

function bandClass(v: number | null, peak: number): string {
  if (v == null) return "";
  const r = v / peak;
  if (r >= 0.95) return "peak";
  if (r >= 0.65) return "high";
  if (r >= 0.35) return "mid";
  return "low";
}

export function PeakHourRibbon({ peak_hour }: Props) {
  const { t } = useTranslation();
  if (peak_hour == null) return null;
  return (
    <div>
      <div className="ov-label">{t("overview.section_peak_hour")}</div>
      <div className="ov-hours">
        {peak_hour.by_hour.map((v, h) => (
          <div
            key={h}
            className={`ov-hour ${bandClass(v, peak_hour.peak_avg_min)}`}
            title={v != null ? `${h.toString().padStart(2, "0")}:00 — ${v.toFixed(1)}` : ""}
          />
        ))}
      </div>
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
