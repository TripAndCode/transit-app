import { useRouteError } from "react-router-dom";
import { useTranslation } from "react-i18next";

/** Route-level error boundary (react-router ``errorElement``). Catches render
 *  and loader errors anywhere in the matched route tree so a single broken
 *  component degrades to a calm inline message instead of a white screen. */
export function RouteError() {
  const { t } = useTranslation();
  const error = useRouteError();
  if (import.meta.env.DEV) console.error(error);
  return (
    <div role="alert" style={{ padding: 24, display: "grid", gap: 12, justifyItems: "start" }}>
      <span style={{ color: "var(--text-primary)" }}>{t("errors.generic")}</span>
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
        {t("common.reload")}
      </button>
    </div>
  );
}
