import type { TFunction } from "i18next";
import type { TierState } from "../../api/useCapabilityTier";
import type {
  CoverageGapReason,
  DwellRunPayload,
  HeadwayQualityResponse,
  WeatherDelayResponse,
} from "../../api/types";
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

/** What a reader is told in place of a probe state: the check's schedule is
 *  the operator's business, so a reader gets either the fact about the feed
 *  or one neutral line that claims nothing about any check. */
const READER_STATUS = "reports.status.unavailable";
const READER_TEXT = {
  feed_unsupported: "reports.coverage.feed_unsupported",
  probe_refuted: "reports.coverage.feed_unsupported",
  probe_expired: "reports.coverage.reader_unavailable",
  never_probed: "reports.coverage.reader_unavailable",
} as const satisfies Record<CoverageGapReason, string>;

/** A basic-tier feed can't support any analysis the coverage gate guards,
 *  so its tier is the true cause, whatever state the gate reports. Until
 *  the tier is known no cause is stated, so a pipeline reason never shows
 *  for a moment before the tier replaces it. Past the tier, only an admin
 *  is shown the probe state itself (its status, its check date); the
 *  analysis is not shown to a reader either way, so the gap keeps its
 *  place and only its wording changes. */
function coverageNotShown(
  t: TFunction,
  reason: CoverageGapReason,
  lastChecked: string | null | undefined,
  tier: TierState,
  isAdmin: boolean,
): NotShown {
  if (tier === "basic") {
    return { status: t("capability.basic.status"), text: t("capability.basic.reason") };
  }
  if (tier === "loading") {
    return { status: t("capability.loading.status"), text: t("capability.loading.text") };
  }
  if (!isAdmin) return { status: t(READER_STATUS), text: t(READER_TEXT[reason]) };
  return {
    status: t(COVERAGE_STATUS[reason]),
    text: t(COVERAGE_TEXT[reason], { date: lastChecked ? formatDate(lastChecked) : "" }),
  };
}

/** The dwell/running split can't be shown whenever the payload says it isn't
 *  available, or when routes matched but none holds a sample; an unavailable
 *  payload naming no reason reads as never checked, the one cause that
 *  claims nothing about the feed. An available payload with no rows and no
 *  reason is an empty scope, which the split's own empty state words. */
export function dwellNotShown(
  t: TFunction,
  payload: DwellRunPayload | undefined,
  tier: TierState,
  isAdmin: boolean,
): NotShown | null {
  const reason = payload?.unavailable_reason;
  if (payload?.available) {
    return reason === "no_paired_samples"
      ? { status: t("reports.status.no_observations"), text: t("reports.dwell_run.no_paired_samples") }
      : null;
  }
  return coverageNotShown(
    t,
    reason == null || reason === "no_paired_samples" ? "never_probed" : reason,
    payload?.last_checked,
    tier,
    isAdmin,
  );
}

/** Headway quality can't be shown when the API names why its rows are bound
 *  to be empty. An empty answer with no reason is a range or day choice the
 *  reader can change, so the panel itself says so instead. */
export function headwayNotShown(
  t: TFunction,
  data: HeadwayQualityResponse,
  tier: TierState,
  isAdmin: boolean,
): NotShown | null {
  const reason = data.unavailable_reason;
  if (data.rows.length > 0 || reason == null) return null;
  if (reason === "no_high_frequency_routes") {
    return { status: t("reports.status.not_applicable"), text: t("reports.headway_quality.no_high_frequency_routes") };
  }
  return coverageNotShown(t, reason, data.last_checked, tier, isAdmin);
}

/** The rain comparison can't be shown at all without a weather station. A
 *  station with no matching day in range is the range's answer, which the
 *  panel gives with the station named. */
export function weatherNotShown(t: TFunction, data: WeatherDelayResponse): NotShown | null {
  if (data.available || data.station != null) return null;
  return { status: t("reports.status.not_set"), text: t("reports.weather_delay.no_station") };
}

/** The slow segments can't be shown when the agency's arrivals aren't
 *  trusted; an unavailable answer naming no reason reads as never checked. */
export function slowZonesNotShown(
  t: TFunction,
  data: { available: boolean; unavailable_reason: CoverageGapReason | null; last_checked: string | null },
  tier: TierState,
  isAdmin: boolean,
): NotShown | null {
  if (data.available) return null;
  return coverageNotShown(t, data.unavailable_reason ?? "never_probed", data.last_checked, tier, isAdmin);
}
