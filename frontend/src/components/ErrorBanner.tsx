import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ApiError, apiErrorDetail, isAggregateNotReady } from "../api/client";

type Props = {
  error: unknown;
  onRetry?: () => void;
};

// Machine-readable detail codes from the Ask follow-up endpoint (see
// `_raise_for_followup_error` in api/routers/conversations.py). Checked
// before the generic status-code branches below so a follow-up failure
// explains *why* (too long / rate-limited / connection) instead of a
// one-size-fits-all message.
const FOLLOWUP_TOO_LONG_DETAIL = "question_too_long";
const FOLLOWUP_LLM_ERROR_PREFIX = "llm_error:";

function messageFor(err: unknown, t: TFunction): string {
  if (err instanceof ApiError) {
    const detail = apiErrorDetail(err);
    if (detail === FOLLOWUP_TOO_LONG_DETAIL) return t("errors.followup_too_long");
    if (detail?.startsWith(FOLLOWUP_LLM_ERROR_PREFIX)) {
      const kind = detail.slice(FOLLOWUP_LLM_ERROR_PREFIX.length);
      if (kind === "rate_limit") return t("errors.followup_rate_limit");
      if (kind === "connection") return t("errors.followup_connection");
      return t("errors.followup_llm_generic");
    }
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

  // Aggregates-not-built (503) is persistent, not transient: explain it calmly
  // in a neutral tone and offer no retry (retrying can't build the data).
  if (isAggregateNotReady(error)) {
    return (
      <div
        role="status"
        style={{
          background: "var(--bg-soft)",
          color: "var(--text-secondary)",
          border: "1px solid var(--border-soft)",
          padding: "10px 14px",
          borderRadius: "var(--radius)",
          margin: "0 0 16px",
          lineHeight: 1.5,
        }}
      >
        {t("errors.aggregate_not_ready")}
      </div>
    );
  }

  return (
    <div
      role="alert"
      style={{
        background: "var(--error-bg)",
        color: "var(--error-fg)",
        border: "1px solid color-mix(in srgb, var(--error-fg) 35%, var(--error-bg))",
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
