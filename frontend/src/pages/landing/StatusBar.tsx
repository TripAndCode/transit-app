import { useTranslation } from "react-i18next";
import { STATUS, ON_TIME_THRESHOLD_SECONDS } from "./welcomeSample";
import { SampleTag } from "./SampleTag";
import { formatNumber } from "../../utils/format";

/** The card directly under the hero stage: the same fixture figures the
 *  stage's title bar summarizes, spelled out, plus the one sample-data
 *  label that covers both. */
export function StatusBar() {
  const { t } = useTranslation();
  return (
    <section className="landing-status" aria-label={t("landing.status.aria_label")}>
      <div className="landing-status__card">
        <div className="landing-status__updated">
          <span className="landing-live" aria-hidden="true" />
          <span>{t("landing.status.updated_at", { datetime: STATUS.updatedAt })}</span>
        </div>
        <div className="landing-status__cadence">
          {t("landing.status.cadence")} · <SampleTag />
        </div>
      </div>
      <div className="landing-status__fig">
        <b>{t("landing.status.feeds_label")}</b>
        <span className="landing-status__fig-value">
          {t("landing.status.feeds_value", { feeds: STATUS.feeds, cities: STATUS.cities })}
        </span>
      </div>
      <div className="landing-status__fig">
        <b>{t("landing.status.buses_label")}</b>
        <span className="landing-status__fig-value">{t("landing.status.buses_value", { count: formatNumber(STATUS.busesTracked) })}</span>
      </div>
      <div className="landing-status__fig">
        <b>{t("landing.status.ontime_label")}</b>
        <span className="landing-status__fig-value landing-status__fig-value--ok">
          {t("landing.status.ontime_value", { pct: STATUS.onTimePct, seconds: ON_TIME_THRESHOLD_SECONDS })}
        </span>
      </div>
    </section>
  );
}
