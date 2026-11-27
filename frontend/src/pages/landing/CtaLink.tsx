import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useCtaKind } from "./useCtaKind";

/** The dashboard link, rendered identically wherever it appears (header
 *  button, hero primary CTA): one CTA decision, from useCtaKind, wherever it
 *  renders. */
export function CtaLink({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { kind } = useCtaKind();
  return (
    <Link to="/" className={className}>
      {t(kind === "session" ? "landing.hero.open_dashboard" : "landing.hero.view_dashboard")}
    </Link>
  );
}
