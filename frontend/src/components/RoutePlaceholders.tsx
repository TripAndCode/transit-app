import { useTranslation } from "react-i18next";

/** Index route placeholder shown while AgencyPicker resolves the redirect. */
export function IndexLoadingPlaceholder() {
  const { t } = useTranslation();
  return <div style={{ padding: 24, color: "var(--text-tertiary)" }}>{t("common.loading_agencies")}</div>;
}

/** Suspense fallback while a lazy route chunk downloads. */
export function ChunkLoading() {
  const { t } = useTranslation();
  return <div style={{ padding: 24, color: "var(--text-tertiary)" }}>{t("common.loading")}</div>;
}
