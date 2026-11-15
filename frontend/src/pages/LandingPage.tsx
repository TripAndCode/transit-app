import { useState } from "react";
import { useTranslation } from "react-i18next";
import { DataSourceCredits } from "../components/DataSourceCredits";
import { LegalLinks } from "../components/LegalLinks";
import { CtaLink } from "./landing/CtaLink";
import { PauseToggle } from "./landing/PauseToggle";
import { WelcomeHero } from "./landing/WelcomeHero";
import { StatusBar } from "./landing/StatusBar";
import { AudienceCards } from "./landing/AudienceCards";
import { FeedTable } from "./landing/FeedTable";
import { CouncilSample } from "./landing/CouncilSample";
import { MethodSteps } from "./landing/MethodSteps";
import { BusTownTeaser } from "./landing/BusTownTeaser";
import "./LandingPage.css";

const NAV_LINKS = [
  { href: "#councils", key: "landing.nav.councils" },
  { href: "#observatory", key: "landing.nav.feeds" },
  { href: "#bus-town", key: "nav.bus-town" },
  { href: "#method", key: "landing.nav.method" },
] as const;

/** Pre-authentication marketing page: a structured walk through what the
 *  product actually shows (nationwide observatory, council reports, feed
 *  grades, Bus Town playback, the calculation method), every figure a
 *  labelled sample fixture, never a live read. It stays reachable after
 *  sign-in and from the app's "about" link, so its one real CTA is the way
 *  in this visitor actually has -- see useCtaKind. The pause toggle is the
 *  one control for every looping animation on the page (WCAG 2.2.2). */
export function LandingPage() {
  const { t } = useTranslation();
  const [paused, setPaused] = useState(false);
  const year = new Date().getFullYear();
  return (
    <div className={paused ? "landing-shell is-paused" : "landing-shell"}>
      <header className="landing-header">
        <div className="landing-header__brand">
          <span className="landing-header__title">{t("header.app_title")}</span>
          <span className="landing-header__tagline">{t("header.app_tagline")}</span>
        </div>
        <nav className="landing-header__nav" aria-label={t("landing.nav.aria_label")}>
          {NAV_LINKS.map((link) => (
            <a key={link.href} href={link.href}>
              {t(link.key)}
            </a>
          ))}
        </nav>
        <div className="landing-header__actions">
          <PauseToggle paused={paused} onToggle={() => setPaused((p) => !p)} />
          <CtaLink className="btn btn--primary landing-header__cta" />
        </div>
      </header>
      <WelcomeHero />
      <StatusBar />
      <AudienceCards />
      <FeedTable />
      <CouncilSample />
      <MethodSteps />
      <BusTownTeaser />
      <footer className="landing-footer">
        <p className="landing-footer__disclaimer">{t("landing.footer.disclaimer")}</p>
        <LegalLinks className="landing-footer__links" />
        <DataSourceCredits className="landing-footer__credits" />
        <p className="landing-footer__copyright">{t("landing.footer.copyright", { year, name: t("header.app_title") })}</p>
      </footer>
    </div>
  );
}
