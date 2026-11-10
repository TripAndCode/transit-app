import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { contentLocale } from "../i18n/contentLocale";
import METHODOLOGY from "./methodology.json";

/** The terms, the privacy policy and the methodology, which is published
 *  beside the code rather than served by the app, so it opens in a new tab
 *  in the reader's language. `methodology.json` is the one list of its
 *  addresses; the server's exports read it too. */
export function LegalLinks({ className }: { className?: string }) {
  const { t, i18n } = useTranslation();
  return (
    <nav className={className} aria-label={t("legal.nav_label")} style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
      <Link to="/terms">{t("legal.terms")}</Link>
      <Link to="/privacy">{t("legal.privacy")}</Link>
      <a href={METHODOLOGY[contentLocale(i18n)]} target="_blank" rel="noreferrer">
        {t("legal.methodology")}
      </a>
    </nav>
  );
}
