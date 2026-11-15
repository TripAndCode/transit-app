import { useTranslation } from "react-i18next";

/** "Who it's for" -- three doors into the same data, each an in-page anchor
 *  to the section that backs it up (decision: links point only to things
 *  that exist on this page; the real Bus Town redirect lives on
 *  BusTownTeaser's own button, not here). */
export function AudienceCards() {
  const { t } = useTranslation();
  return (
    <section className="landing-section">
      <div className="landing-eyebrow">{t("landing.doors.eyebrow")}</div>
      <h2 className="landing-h2">{t("landing.doors.heading")}</h2>
      <div className="landing-doors">
        <a className="landing-card landing-door" href="#councils">
          <span className="landing-door__who">{t("landing.doors.councils.who")}</span>
          <span className="landing-door__title">{t("landing.doors.councils.title")}</span>
          <span className="landing-door__body">{t("landing.doors.councils.body")}</span>
          <span className="landing-door__chips">
            <span className="landing-chip">{t("landing.chips.pdf")}</span>
            <span className="landing-chip">{t("landing.chips.csv")}</span>
            <span className="landing-chip">{t("landing.chips.print")}</span>
          </span>
          <span className="landing-door__go">{t("landing.doors.councils.cta")}</span>
        </a>
        <a className="landing-card landing-door" href="#observatory">
          <span className="landing-door__who">{t("landing.doors.operators.who")}</span>
          <span className="landing-door__title">{t("landing.doors.operators.title")}</span>
          <span className="landing-door__body">{t("landing.doors.operators.body")}</span>
          <span className="landing-door__chips">
            <span className="landing-chip">{t("landing.doors.operators.chip_monthly")}</span>
            <span className="landing-chip">{t("landing.doors.operators.chip_gaps")}</span>
          </span>
          <span className="landing-door__go">{t("landing.doors.operators.cta")}</span>
        </a>
        <a className="landing-card landing-door" href="#bus-town">
          <span className="landing-door__who">{t("landing.doors.everyone.who")}</span>
          <span className="landing-door__title">{t("nav.bus-town")}</span>
          <span className="landing-door__body">{t("landing.doors.everyone.body")}</span>
          <span className="landing-door__chips">
            <span className="landing-chip">{t("landing.doors.everyone.chip_no_signup")}</span>
            <span className="landing-chip">{t("landing.doors.everyone.chip_mobile")}</span>
          </span>
          <span className="landing-door__go">{t("landing.doors.everyone.cta", { name: t("nav.bus-town") })}</span>
        </a>
      </div>
    </section>
  );
}
