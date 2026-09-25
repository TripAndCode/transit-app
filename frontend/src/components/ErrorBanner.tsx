import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ApiError, apiErrorDetail } from "../api/client";
import { classifyError, type ErrorClass } from "../api/errorClass";

type Props = {
  error: unknown;
  onRetry?: () => void;
  /** Overrides the class-derived text. For a failure whose meaning is the
   *  action rather than the transport -- "couldn't clear the override" says
   *  more than "server error" -- so that such a case can still use this
   *  banner instead of hand-rolling one beside it. */
  message?: string;
};

// Machine-readable detail codes from the Ask follow-up endpoint (see
// `_raise_for_followup_error` in api/routers/conversations.py). Checked
// before the class-driven branches below so a follow-up failure explains
// *why* (too long / rate-limited / connection) instead of a one-size-fits-all
// message.
const FOLLOWUP_TOO_LONG_DETAIL = "question_too_long";
const FOLLOWUP_LLM_ERROR_PREFIX = "llm_error:";

function followupDetailMessage(err: unknown, t: TFunction): string | null {
  if (!(err instanceof ApiError)) return null;
  const detail = apiErrorDetail(err);
  if (detail === FOLLOWUP_TOO_LONG_DETAIL) return t("errors.followup_too_long");
  if (detail?.startsWith(FOLLOWUP_LLM_ERROR_PREFIX)) {
    const kind = detail.slice(FOLLOWUP_LLM_ERROR_PREFIX.length);
    if (kind === "rate_limit") return t("errors.followup_rate_limit");
    if (kind === "connection") return t("errors.followup_connection");
    return t("errors.followup_llm_generic");
  }
  return null;
}

function messageFor(err: unknown, cls: ErrorClass, t: TFunction): string {
  const followup = followupDetailMessage(err, t);
  if (followup) return followup;
  switch (cls) {
    case "rate_limited":
      return t("errors.rate_limited");
    case "not_found":
      return t("errors.not_found");
    case "server":
      return t("errors.server_5xx");
    case "timeout":
      return t("errors.timeout");
    case "network":
      return t("errors.network");
    case "generic":
      return err instanceof ApiError ? t("errors.generic_status", { status: err.status }) : t("errors.generic");
    default:
      return t("errors.generic");
  }
}

/** A calm, no-retry status banner for a standing condition (not a service
 *  problem, not something a retry or a re-login fixes). */
function CalmStatus({ children }: { children: ReactNode }) {
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
      {children}
    </div>
  );
}

/** The loud branch: something went wrong and saying so is the point. */
function Alert({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  const { t } = useTranslation();
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
      <span style={{ flex: 1 }}>{children}</span>
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

export function ErrorBanner({ error, onRetry, message }: Props) {
  const { t } = useTranslation();
  const cls = classifyError(error);

  // An explicit message wins over every branch below, including the calm
  // ones: a caller that names the failure knows something the error class
  // cannot.
  if (message != null) return <Alert onRetry={onRetry}>{message}</Alert>;

  // Admin-approval-required 403 (Copilot insight, Ask follow-up) — a standing
  // condition until an admin flips users.llm_approved, not a service problem
  // or something signing in again fixes, so this gets a calm explanation with
  // no login link and no retry button.
  if (cls === "not_approved") return <CalmStatus>{t("errors.llm_not_approved")}</CalmStatus>;

  // Aggregates-not-built (503) is persistent, not transient: explain it calmly
  // in a neutral tone and offer no retry (retrying can't build the data).
  if (cls === "not_ready") return <CalmStatus>{t("errors.aggregate_not_ready")}</CalmStatus>;

  return <Alert onRetry={onRetry}>{messageFor(error, cls, t)}</Alert>;
}
