import { Link } from "react-router-dom";
import { Trans, useTranslation } from "react-i18next";
import { useSession } from "../api/auth";
import { useConfig } from "../api/config";
import { DataSourceCredits } from "../components/DataSourceCredits";
import { LegalLinks } from "../components/LegalLinks";
import { LiveMapHero } from "./landing/LiveMapHero";
import { ScrollNarrative } from "./landing/ScrollNarrative";
import "./LandingPage.css";

/** Pre-authentication marketing/landing page -- a cinematic first
 *  impression kept deliberately separate from the calm, data-dense signed-
 *  in dashboard (AGENTS.md's "keep UI calm" rule governs the working
 *  Overview/Map/Analysis/Agencies/Live/Ask tabs, not this page). It stays
 *  reachable after sign-in and from the app's "about" link, so its one CTA
 *  is the way in this visitor actually has: the dashboard for a signed-in
 *  user or, on a deployment that serves signed-out visitors, for a guest;
 *  sign-in otherwise. The hero (animated live-map scene + headline + that
 *  CTA) is the entry point; below it, `ScrollNarrative` mounts three real,
 *  working chart components (fed by static fixtures, not live data) telling
 *  the product's story. */
export function LandingPage() {
  const { t } = useTranslation();
  const { data: session } = useSession();
  const { data: config } = useConfig();
  // Only a configuration known to let signed-out visitors in sends a guest
  // to "/": while it is unknown, or when sign-in is required, "/" would only
  // bounce them back here.
  const guest = !session && config != null && !config.login_required;
  return (
    <div className="landing-shell">
      <section className="landing-hero">
        <LiveMapHero />
        {/* The scrim sits above the canvas and below the text content in
            DOM order (canvas, scrim, content) and keeps the headline legible
            whatever the map is doing underneath. Reordering these -- or
            moving the content above it without an explicit stacking context
            -- lets the scene paint over the text again. The scene's playback
            control comes with the canvas and lifts itself above both. */}
        <div className="landing-hero__scrim" aria-hidden="true" />
        <div className="landing-hero__content">
          <div className="landing-hero__brand">{t("header.app_title")}</div>
          <span className="landing-hero__eyebrow">{t("header.app_tagline")}</span>
          {/* Two spans so the headline always breaks between its two questions,
              never mid-phrase; the space keeps the accessible name one sentence. */}
          <h1 className="landing-hero__title">
            <span className="landing-hero__title-line">{t("landing.hero.title_now")}</span>{" "}
            <span className="landing-hero__title-line">{t("landing.hero.title_where")}</span>
          </h1>
          <p className="landing-hero__subtitle">{t("landing.hero.subtitle")}</p>
          {session ? (
            <Link to="/" className="landing-hero__cta">
              {t("landing.hero.open_dashboard")}
            </Link>
          ) : guest ? (
            <>
              <Link to="/" className="landing-hero__cta">
                {t("landing.hero.view_dashboard")}
              </Link>
              {config.auth_enabled && (
                <p className="landing-hero__note">
                  <Trans t={t} i18nKey="landing.hero.login_note" components={{ login: <Link to="/login" /> }} />
                </p>
              )}
            </>
          ) : (
            <Link to="/login" className="landing-hero__cta">
              {t("common.login")}
            </Link>
          )}
        </div>
      </section>
      <ScrollNarrative />
      <footer className="landing-footer">
        <LegalLinks className="landing-footer__links" />
        <DataSourceCredits className="landing-footer__credits" />
      </footer>
    </div>
  );
}
