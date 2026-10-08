import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useSession } from "../api/auth";
import { LegalLinks } from "../components/LegalLinks";
import { LiveMapHero } from "./landing/LiveMapHero";
import { ScrollNarrative } from "./landing/ScrollNarrative";
import "./LandingPage.css";

/** Pre-authentication marketing/landing page -- a cinematic first
 *  impression kept deliberately separate from the calm, data-dense signed-
 *  in dashboard (AGENTS.md's "keep UI calm" rule governs the working
 *  Overview/Map/Analysis/Agencies/Live/Ask tabs, not this page). It stays
 *  reachable after sign-in, so its one CTA is sign-in for a visitor and the
 *  dashboard for a signed-in user. The hero (animated live-map scene +
 *  headline + that CTA) is the entry point; below it, `ScrollNarrative` mounts three real, working
 *  chart components (fed by static fixtures, not live data) telling the
 *  product's story, rather than the retired `DashboardPreview` mocked
 *  sidebar shell. */
export function LandingPage() {
  const { t } = useTranslation();
  const { data: session } = useSession();
  return (
    <div className="landing-shell">
      <section className="landing-hero">
        <LiveMapHero />
        {/* The scrim sits above the canvas and below the text content in
            DOM order (canvas, scrim, content) and keeps the headline legible
            whatever the map is doing underneath. Reordering these -- or
            moving the content above it without an explicit stacking context
            -- lets the scene paint over the text again. */}
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
          <Link to={session ? "/" : "/login"} className="landing-hero__cta">
            {session ? t("landing.hero.open_dashboard") : t("common.login")}
          </Link>
        </div>
      </section>
      <ScrollNarrative />
      <footer className="landing-footer">
        <LegalLinks className="landing-footer__links" />
      </footer>
    </div>
  );
}
