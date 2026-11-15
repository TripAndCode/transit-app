import { useTranslation } from "react-i18next";
import { COLLECT_TICKS, COLLECT_GAP_MINUTES, COMPARE_SAMPLE, PUBLISH_SAMPLE, HONESTY_UNOBSERVED_STOPS, ON_TIME_THRESHOLD_SECONDS } from "./welcomeSample";
import { formatDuration } from "../../utils/format";
import { SampleTag } from "./SampleTag";

const HONESTY_KEYS = [
  "landing.method.honesty_coverage",
  "landing.method.honesty_gaps",
  "landing.method.honesty_changelog",
] as const;

/** HOW WE KNOW: the three-step pipeline (collect/compare/publish) plus the
 *  "what we don't hide" honesty list, each step illustrated with a small,
 *  purely decorative fixture viz. */
export function MethodSteps() {
  const { t } = useTranslation();
  return (
    <section id="method" className="landing-section">
      <div className="landing-eyebrow">{t("landing.method.eyebrow")}</div>
      <h2 className="landing-h2">{t("landing.method.heading")}</h2>
      <p className="landing-lead">{t("landing.method.lead")}</p>
      <SampleTag />
      <div className="landing-method__flow" aria-hidden="true">
        <span className="landing-method__flow-track" />
        <span className="landing-method__flow-dot" style={{ left: "16.6%" }} />
        <span className="landing-method__flow-dot" style={{ left: "50%" }} />
        <span className="landing-method__flow-dot" style={{ left: "83.3%" }} />
        <span className="landing-method__flow-pulse" />
      </div>
      <div className="landing-method__steps">
        <div className="landing-card landing-method__step">
          <span className="landing-method__step-label">{t("landing.method.step1_label")}</span>
          <b className="landing-method__step-title">{t("landing.method.step1_title")}</b>
          <span className="landing-muted">{t("landing.method.step1_body")}</span>
          <div className="landing-method__viz">
            <div className="landing-method__ticks">
              {COLLECT_TICKS.map((tick, i) => (
                <span
                  key={i}
                  className={tick.gap ? "landing-method__tick landing-method__tick--gap" : "landing-method__tick"}
                  style={{ height: `${tick.heightPct}%` }}
                />
              ))}
            </div>
            <div className="landing-method__tick-labels">
              <span>08:00</span>
              <span>{t("landing.method.step1_gap_caption", { minutes: COLLECT_GAP_MINUTES })}</span>
              <span>08:23</span>
            </div>
          </div>
        </div>
        <div className="landing-card landing-method__step">
          <span className="landing-method__step-label">{t("landing.method.step2_label")}</span>
          <b className="landing-method__step-title">{t("landing.method.step2_title")}</b>
          <span className="landing-muted">{t("landing.method.step2_body")}</span>
          <div className="landing-method__viz landing-method__compare">
            <span>{t("landing.method.step2_scheduled")}</span>
            <span className="landing-method__compare-value">{COMPARE_SAMPLE.scheduled}</span>
            <span>{t("landing.method.step2_observed")}</span>
            <span className="landing-method__compare-value">{COMPARE_SAMPLE.observed}</span>
            <span>{t("landing.method.step2_difference")}</span>
            <span>
              <span className="landing-method__delta">
                {t("landing.method.step2_delta", { delta: formatDuration(COMPARE_SAMPLE.deltaMinutes * 60 + COMPARE_SAMPLE.deltaSeconds) })}
              </span>
            </span>
          </div>
        </div>
        <div className="landing-card landing-method__step">
          <span className="landing-method__step-label">{t("landing.method.step3_label")}</span>
          <b className="landing-method__step-title">{t("landing.method.step3_title")}</b>
          <span className="landing-muted">{t("landing.method.step3_body")}</span>
          <div className="landing-method__viz landing-method__formulas">
            <span>{t("landing.method.step3_formula_ontime", { seconds: ON_TIME_THRESHOLD_SECONDS })}</span>
            <span>{t("landing.method.step3_formula_coverage")}</span>
            <span>{t("landing.method.step3_definition_version", { version: PUBLISH_SAMPLE.definitionVersion, date: PUBLISH_SAMPLE.revisedDate })}</span>
          </div>
        </div>
      </div>
      <div className="landing-method__honesty">
        <b>{t("landing.method.honesty_title")}</b>
        {HONESTY_KEYS.map((key) => (
          <span className="landing-honesty-chip" key={key}>
            {t(key)}
          </span>
        ))}
        <span className="landing-honesty-chip">{t("landing.method.honesty_unobserved_stops", { n: HONESTY_UNOBSERVED_STOPS })}</span>
      </div>
    </section>
  );
}
