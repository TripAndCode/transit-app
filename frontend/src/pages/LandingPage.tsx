import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LiveMapHero } from "./landing/LiveMapHero";
import { ScrollNarrative } from "./landing/ScrollNarrative";
import "./LandingPage.css";

/** Pre-authentication marketing/landing page -- a cinematic first
 *  impression kept deliberately separate from the calm, data-dense signed-
 *  in dashboard (CLAUDE.md's "keep UI calm" rule governs the working
 *  Overview/Map/Analysis/Agencies/Live/Ask tabs, not this page). The hero
 *  (animated live-map scene + headline + sign-in CTA, plus a lower-emphasis "continue
 *  as a guest" link to the already-guest-accessible root route) is the
 *  entry point; below it, `ScrollNarrative` mounts three real, working
 *  chart components (fed by static fixtures, not live data) telling the
 *  product's story, rather than the retired `DashboardPreview` mocked
 *  sidebar shell. */
export function LandingPage() {
  const { t } = useTranslation();
  return (
    <div className="landing-shell">
      <section className="landing-hero theme-scope-dark">
        <LiveMapHero />
        {/* Both overlays sit above the canvas and below the text content in
            DOM order (canvas, vignette, scrim, content): the vignette fades
            the scene's edges, the scrim is the dark horizontal band that
            keeps the headline legible regardless of what the animation is
            doing underneath. Reordering these -- or moving the content
            above them without an explicit stacking context -- lets the
            scene paint over the text again. */}
        <div className="landing-hero__vignette" aria-hidden="true" />
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
          <Link to="/login" className="landing-hero__cta">
            {t("common.login")}
          </Link>
          {/* Lower-emphasis text link, not a second same-weight button: the
              app already allows guest browsing (App.tsx renders
              GuestPrompt with no auth guard on the root route), so this
              link keeps the page honest about that rather than granting
              new access. One obvious default action (sign in) plus one
              clearly secondary, still-discoverable alternative. */}
          <Link to="/" className="landing-hero__guest-cta">
            {t("landing.hero.guest_cta")}
          </Link>
        </div>
      </section>
      <ScrollNarrative />
    </div>
  );
}
