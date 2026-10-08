import { useTranslation } from "react-i18next";

/** Mounted in place of the app when the active language's strings could not
 *  be fetched. Its own strings live in the design namespace, which ships in
 *  the entry, so it never renders bare keys. */
export function LocaleUnavailable() {
  const { t } = useTranslation("design");
  return (
    <div role="alert" style={{ padding: 24, display: "grid", gap: 12, justifyItems: "start" }}>
      <span style={{ color: "var(--text-primary)" }}>{t("stringsUnavailable")}</span>
      <button
        type="button"
        onClick={() => window.location.reload()}
        style={{
          background: "transparent",
          border: "1px solid var(--text-tertiary)",
          color: "var(--text-primary)",
          padding: "4px 10px",
          borderRadius: 4,
          cursor: "pointer",
        }}
      >
        {t("reload")}
      </button>
    </div>
  );
}
