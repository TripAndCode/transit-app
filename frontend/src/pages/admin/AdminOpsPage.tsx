import { useTranslation } from "react-i18next";

export function AdminOpsPage() {
  const { t } = useTranslation();
  return (
    <div style={{ padding: 24 }}>
      <p style={{ color: "var(--text-tertiary)" }}>{t("admin.ops.stub")}</p>
    </div>
  );
}
