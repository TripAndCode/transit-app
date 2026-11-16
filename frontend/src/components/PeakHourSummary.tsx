import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import type { OverviewPeakHour } from "../api/types";
import { fixedTenth, formatHourRange } from "../utils/format";

/** Pulse's heaviest hour as one sentence, and a link to Time, where the
 *  period's delay is drawn by day and hour. */
export function PeakHourSummary({ peak_hour, timeHref }: { peak_hour: OverviewPeakHour; timeHref: string }) {
  const { t } = useTranslation();
  return (
    <div className="ov-card">
      <p className="ov-card-eyebrow">{t("overview.section_peak_hour")}</p>
      <p className="ov-pareto-rest">
        {t("overview.peak_hour_callout", {
          range: formatHourRange(peak_hour.peak_hour),
          avg: fixedTenth(peak_hour.peak_avg_min),
        })}
      </p>
      <Link className="ov-card-link" to={timeHref}>
        {t("overview.peak_hour.see_time")}
      </Link>
    </div>
  );
}
