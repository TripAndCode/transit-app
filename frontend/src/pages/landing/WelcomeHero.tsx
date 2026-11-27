import { useTranslation } from "react-i18next";
import { CtaLink } from "./CtaLink";
import { ObservatoryStage } from "./ObservatoryStage";

const CHECK_KEYS = [
  "landing.hero.check_public_data",
  "landing.hero.check_published_formula",
  "landing.hero.check_unobserved_flagged",
] as const;

export function WelcomeHero() {
  const { t } = useTranslation();
  return (
    <section id="top" className="landing-hero">
      <div className="landing-hero__text">
        <span className="landing-tag landing-hero__live-badge">
          <span className="landing-live" aria-hidden="true" />
          {t("landing.hero.live_badge")}
        </span>
        <h1 className="landing-hero__title">{t("landing.hero.title")}</h1>
        <p className="landing-hero__lead">{t("landing.hero.lead")}</p>
        <div className="landing-hero__ctas">
          <CtaLink className="btn btn--primary landing-hero__cta" />
          <a className="btn btn--secondary landing-hero__cta" href="#observatory">
            {t("landing.hero.secondary_cta")}
          </a>
        </div>
        <div className="landing-hero__checks">
          {CHECK_KEYS.map((key) => (
            <span className="landing-check landing-check--sm" key={key}>
              {t(key)}
            </span>
          ))}
        </div>
      </div>
      <ObservatoryStage />
    </section>
  );
}
