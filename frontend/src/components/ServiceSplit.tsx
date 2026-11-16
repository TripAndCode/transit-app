import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import type { OverviewDayType, OverviewServiceSplitEntry } from "../api/types";
import { formatMinutes, roundTenth } from "../utils/format";
import type { LabelT } from "../utils/filterValueLabels";
import { DAY_TYPE_KEY } from "./dayTypeKey";

/** A bucket whose mean is compared: it has data, and enough of it. */
function comparable(entry: OverviewServiceSplitEntry): entry is OverviewServiceSplitEntry & { avg_min: number } {
  return entry.avg_min != null && !entry.insufficient;
}

function dayTypeLabel(type: OverviewDayType, t: LabelT): string {
  return t(DAY_TYPE_KEY[type]);
}

/** Two tied day types read as one subject ("Weekdays and Saturdays"). Three
 *  can never tie for one end without the ends meeting. */
function joinLabels(types: OverviewDayType[], t: LabelT): string {
  const labels = types.map((type) => dayTypeLabel(type, t));
  return labels.length === 2 ? t("overview.service_split.pair", { a: labels[0], b: labels[1] }) : labels[0];
}

/** The slowest day type against the fastest, judged on the one-decimal means
 *  the sentence prints, so it never states a gap it rounds to nothing. Null
 *  when fewer than two day types have enough data to compare. */
function serviceSplitStory(entries: OverviewServiceSplitEntry[], t: LabelT): string | null {
  const shown = entries.filter(comparable).map((e) => ({ type: e.day_type, v: roundTenth(e.avg_min) }));
  if (shown.length < 2) return null;
  const hi = Math.max(...shown.map((s) => s.v));
  const lo = Math.min(...shown.map((s) => s.v));
  if (hi === lo) return t("overview.service_split.story_same");
  return t("overview.service_split.story_slowest", {
    slowest: joinLabels(shown.filter((s) => s.v === hi).map((s) => s.type), t),
    fastest: joinLabels(shown.filter((s) => s.v === lo).map((s) => s.type), t),
    diff: formatMinutes(hi - lo),
  });
}

/** Pulse's day-type figure: one sentence comparing the period's day types,
 *  and a link to Day types & agencies, where weekday against weekend is
 *  compared route by route. */
export function ServiceSplit({
  service_split,
  compareHref,
}: {
  /** Every bucket the agency is split into, in display order. */
  service_split: OverviewServiceSplitEntry[];
  compareHref: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="ov-card">
      <p className="ov-card-eyebrow">{t("overview.section_service_split")}</p>
      <p className="ov-svc-story">{serviceSplitStory(service_split, t) ?? t("overview.service_split.story_none")}</p>
      <Link className="ov-card-link" to={compareHref}>
        {t("overview.service_split.see_compare")}
      </Link>
    </div>
  );
}
