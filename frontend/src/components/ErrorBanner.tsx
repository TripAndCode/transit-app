import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ApiError } from "../api/client";

type Props = {
  error: unknown;
  onRetry?: () => void;
};

function messageFor(err: unknown, t: TFunction): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return t("errors.rate_limited");
    if (err.status === 404) return t("errors.not_found");
    if (err.status >= 500) return t("errors.server_5xx");
    return t("errors.generic_status", { status: err.status });
  }
  if (err instanceof Error) return t("errors.network");
  return t("errors.generic");
}

export function ErrorBanner({ error, onRetry }: Props) {
  const { t } = useTranslation();
  return (
    <div
      role="alert"
      style={{
        background: "var(--error-bg)",
        color: "var(--error-fg)",
        border: "1px solid #f0e2b6",
        padding: "10px 14px",
        borderRadius: "var(--radius)",
        display: "flex",
        alignItems: "center",
        gap: 12,
        margin: "0 0 16px",
      }}
    >
      <span style={{ flex: 1 }}>{messageFor(error, t)}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          style={{
            background: "transparent",
            border: "1px solid currentColor",
            color: "inherit",
            padding: "4px 10px",
            borderRadius: 4,
          }}
        >
          {t("common.retry")}
        </button>
      )}
    </div>
  );
}
