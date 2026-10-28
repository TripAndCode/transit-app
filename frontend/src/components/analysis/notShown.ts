import type { TFunction } from "i18next";
import type { CoverageGapReason, DwellRunPayload, HeadwayQualityResponse, WeatherDelayResponse } from "../../api/types";
import { formatDate } from "../../utils/format";

/** An analysis that can't be shown: the status it carries and the one
 *  sentence that gives the true cause. */
export type NotShown = { status: string; text: string };

// A feed that can't carry the fields, or was found not to send them, is
// unsupported; a lapsed check awaits a re-check; a check that never ran is
// unchecked.
const COVERAGE_STATUS = {
  feed_unsupported: "reports.status.unsupported",
  probe_refuted: "reports.status.unsupported",
  probe_expired: "reports.status.recheck",
  never_probed: "reports.status.unchecked",
} as const satisfies Record<CoverageGapReason, string>;

const COVERAGE_TEXT = {
  feed_unsupported: "reports.coverage.feed_unsupported",
  probe_refuted: "reports.coverage.probe_refuted",
  probe_expired: "reports.coverage.probe_expired",
  never_probed: "reports.coverage.never_probed",
} as const satisfies Record<CoverageGapReason, string>;

function coverageNotShown(t: TFunction, reason: CoverageGapReason, lastChecked: string | null | undefined): NotShown {
  return {
    status: t(COVERAGE_STATUS[reason]),
    text: t(COVERAGE_TEXT[reason], { date: lastChecked ? formatDate(lastChecked) : "" }),
  };
}

/** The dwell/running split can't be shown whenever the payload says it isn't
 *  available; a payload naming no reason reads as never checked, the one
 *  cause that claims nothing about the feed. */
export function dwellNotShown(t: TFunction, payload: DwellRunPayload | undefined): NotShown | null {
  if (payload?.available) return null;
  return coverageNotShown(t, payload?.unavailable_reason ?? "never_probed", payload?.last_checked);
}

/** Headway quality can't be shown when the API names why its rows are bound
 *  to be empty. An empty answer with no reason is a range or day choice the
 *  reader can change, so the panel itself says so instead. */
export function headwayNotShown(t: TFunction, data: HeadwayQualityResponse): NotShown | null {
  const reason = data.unavailable_reason;
  if (data.rows.length > 0 || reason == null) return null;
  if (reason === "no_high_frequency_routes") {
    return { status: t("reports.status.not_applicable"), text: t("reports.headway_quality.no_high_frequency_routes") };
  }
  return coverageNotShown(t, reason, data.last_checked);
}

/** The rain comparison can't be shown at all without a weather station. A
 *  station with no matching day in range is the range's answer, which the
 *  panel gives with the station named. */
export function weatherNotShown(t: TFunction, data: WeatherDelayResponse): NotShown | null {
  if (data.available || data.station != null) return null;
  return { status: t("reports.status.not_set"), text: t("reports.weather_delay.no_station") };
}
