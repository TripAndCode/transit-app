import { useTranslation } from "react-i18next";
import { Spinner } from "./Spinner";

/** Shown by OnboardingGate while agencies load. */
export function IndexLoadingPlaceholder() {
  const { t } = useTranslation();
  return <div style={{ padding: 24, color: "var(--text-tertiary)" }}>{t("common.loading_agencies")}</div>;
}

/**
 * Suspense fallback while a lazy route chunk downloads. Hit on every first
 * visit to any lazy-loaded tab, so it reuses the app's shared Spinner rather
 * than bare text, matching the loading treatment used elsewhere (e.g. AskTab).
 */
export function ChunkLoading() {
  const { t } = useTranslation();
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        padding: 24,
        color: "var(--text-tertiary)",
      }}
    >
      <Spinner size={16} />
      <span>{t("common.loading")}</span>
    </div>
  );
}
