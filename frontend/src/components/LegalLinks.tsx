import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

export function LegalLinks({ className }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <nav className={className} aria-label={t("legal.nav_label")} style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
      <Link to="/terms">{t("legal.terms")}</Link>
      <Link to="/privacy">{t("legal.privacy")}</Link>
    </nav>
  );
}
