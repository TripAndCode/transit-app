import { useTranslation } from "react-i18next";
import { FEW_DATA_MIN_SAMPLES, isFewData } from "./fewData";

/**
 * The few-data mark after a figure. A sample count may be hidden only while
 * the figures it qualifies carry this mark when the count is thin, so a
 * figure resting on a handful of observations never reads as a solid one.
 */
export function FewDataMark({ samples, floor = FEW_DATA_MIN_SAMPLES }: { samples: unknown; floor?: number | null }) {
  const { t } = useTranslation();
  if (!isFewData(samples, floor)) return null;
  return (
    <>
      {" "}
      <span className="caveat-badge">{t("reports.confidence_low_mark")}</span>
    </>
  );
}
