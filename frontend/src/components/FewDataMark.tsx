import { useTranslation } from "react-i18next";
import { FEW_DATA_MIN_SAMPLES, isFewData } from "./fewData";

/**
 * The few-data mark after a figure. A sample count may be hidden only while
 * the figures it qualifies carry this mark when the count is thin, so a
 * figure resting on a handful of observations never reads as a solid one.
 * `figure` is the value it sits beside: a missing figure ("—") says nothing
 * to qualify, so it carries no mark.
 */
export function FewDataMark({
  figure,
  samples,
  floor = FEW_DATA_MIN_SAMPLES,
}: {
  figure: unknown;
  samples: unknown;
  floor?: number | null;
}) {
  const { t } = useTranslation();
  if (figure == null || !isFewData(samples, floor)) return null;
  return (
    <>
      {" "}
      <span className="caveat-badge">{t("reports.confidence_low_mark")}</span>
    </>
  );
}
