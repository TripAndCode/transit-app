import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { OverviewHeadline, OverviewHeadlineReference } from "../../api/types";
import { InsightHint } from "../InsightHint";
import { fixedTenth, formatDateRange, formatNumber } from "../../utils/format";
import { FLAT_MAX_DELTA_MIN, FLAT_MAX_DELTA_PCT, referenceJudgement, windowDays } from "./storySentence";

type Props = {
  headline: OverviewHeadline;
  /** Whether the verdict compares the headline with the same number of days
   *  just before it. */
  hasBaseline: boolean;
};

/** The reference's figure, or why it has none, as one line. */
function describe(ref: OverviewHeadlineReference, range: string, t: TFunction): string {
  const vars = { days: ref.days, range };
  switch (ref.reason) {
    case "no_data":
      return t("overview.reference.no_data", vars);
    case "insufficient_days":
      return t("overview.reference.insufficient_days", { ...vars, count: ref.days_collected, min: ref.min_days });
    case "insufficient_samples":
      return t("overview.reference.insufficient_samples", {
        ...vars,
        samples: formatNumber(ref.samples),
        min: formatNumber(ref.min_samples),
      });
    case "not_aggregated":
      return t("overview.reference.not_aggregated", vars);
    case null:
      if (ref.avg_min == null) return t("overview.reference.no_data", vars);
      return ref.days_collected < ref.days_selected
        ? t("overview.reference.line_partial", { ...vars, count: ref.days_collected, value: fixedTenth(ref.avg_min) })
        : t("overview.reference.line", { ...vars, value: fixedTenth(ref.avg_min) });
  }
}

/**
 * The headline's trailing reference: its own figure over the days before the
 * period, under the same scope, and a judgement against it by the verdict's
 * tolerance (`referenceJudgement`). A withheld reference states why rather
 * than printing a figure.
 *
 * When the window just before the period is as wide as the reference, it is
 * the reference's own window, and the verdict already states that comparison
 * in numbers, so nothing is repeated here.
 */
export function HeadlineReference({ headline, hasBaseline }: Props) {
  const { t } = useTranslation();
  const ref = headline.reference;
  if (!ref) return null;
  if (hasBaseline && windowDays(headline.window_from, headline.window_to) === ref.days) return null;

  const range = formatDateRange(ref.window_from, ref.window_to, { year: false });
  const judgement = ref.reason == null ? referenceJudgement(headline.avg_min, ref) : null;
  // A scope with no aggregate read no days, so its counts are 0 and the
  // floors they would state are left out.
  const coverage =
    ref.reason === "not_aggregated"
      ? ""
      : t("overview.reference.hint_coverage", {
          selected: ref.days_selected,
          min_days: ref.min_days,
          min_samples: formatNumber(ref.min_samples),
        });
  return (
    <div className="ov-hero-reference">
      <span className="ov-hero-reference-text">{describe(ref, range, t)}</span>
      {judgement && (
        <span className="ov-hero-reference-chip">{t(`overview.reference.judgement_${judgement}`, { days: ref.days })}</span>
      )}
      <InsightHint
        title={t("overview.reference.hint_title", { days: ref.days })}
        body={t("overview.reference.hint_body", {
          days: ref.days,
          range,
          coverage,
          flat_min: FLAT_MAX_DELTA_MIN,
          flat_pct: FLAT_MAX_DELTA_PCT,
        })}
      />
    </div>
  );
}
