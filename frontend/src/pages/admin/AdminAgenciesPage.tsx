import { useTranslation } from "react-i18next";

export function AdminAgenciesPage() {
  const { t } = useTranslation();
  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{t("admin.nav.agencies")}</h1>
    </div>
  );
}
