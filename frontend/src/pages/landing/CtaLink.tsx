import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useCtaKind } from "./useCtaKind";

/** The dashboard/sign-in link, rendered identically wherever it appears
 *  (header button, hero primary CTA): one CTA decision, from useCtaKind,
 *  wherever it renders. */
export function CtaLink({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { kind } = useCtaKind();
  if (kind === "session") {
    return (
      <Link to="/" className={className}>
        {t("landing.hero.open_dashboard")}
      </Link>
    );
  }
  if (kind === "guest") {
    return (
      <Link to="/" className={className}>
        {t("landing.hero.view_dashboard")}
      </Link>
    );
  }
  return (
    <Link to="/login" className={className}>
      {t("common.login")}
    </Link>
  );
}
