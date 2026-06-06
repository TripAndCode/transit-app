/**
 * DataStalenessBanner — calm warning when GTFS ingest hasn't run recently.
 *
 * Reads the same /today/route-summary endpoint that LiveTab uses (shared
 * cache, no extra request). When the latest observation is more than
 * STALE_THRESHOLD_HOURS old, render a soft warning pill above the main
 * content so users know they're looking at historical data even when the
 * rest of the UI reads as live.
 *
 * Dismissible per session — re-appears on full page reload to avoid
 * permanently hiding the signal.
 */
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../api/hooks";

const STALE_THRESHOLD_HOURS = 24;
const SESSION_DISMISS_KEY = "ds_banner_dismissed_at";

function relativeAgeHours(iso: string): number {
  const captured = new Date(iso).getTime();
  if (!Number.isFinite(captured)) return 0;
  return (Date.now() - captured) / (1000 * 60 * 60);
}

export function DataStalenessBanner() {
  const { agencyId: raw } = useParams();
  const agencyId = raw ? Number(raw) : null;
  const { t, i18n } = useTranslation();
  const { data } = useTodayRouteSummary(agencyId, { autoRefresh: false });
  // sessionStorage is only ever written by handleDismiss in this same tab,
  // so a lazy initializer + the handler's own setState cover every path —
  // the previous re-read effect was a no-op (the key is agency-independent).
  const [dismissed, setDismissed] = useState(
    () => sessionStorage.getItem(SESSION_DISMISS_KEY) != null,
  );

  if (agencyId == null) return null;
  const captured = data?.latest_captured_at;
  if (!captured) return null;
  const ageH = relativeAgeHours(captured);
  if (ageH < STALE_THRESHOLD_HOURS) return null;
  if (dismissed) return null;

  const days = Math.floor(ageH / 24);
  const ageLabel =
    days >= 1
      ? t("common.rel_days_ago", { count: days })
      : t("common.rel_hours_ago", { count: Math.floor(ageH) });

  function handleDismiss() {
    sessionStorage.setItem(SESSION_DISMISS_KEY, String(Date.now()));
    setDismissed(true);
  }

  // Choose a calm warm-tan color (max saturation 50%) — never alarm red.
  const bg = "hsl(38, 50%, 95%)";
  const fg = "hsl(28, 45%, 32%)";
  const border = "hsl(38, 45%, 78%)";

  return (
    <div
      role="status"
      aria-live="polite"
      lang={i18n.language}
      style={{
        background: bg,
        color: fg,
        borderBottom: `1px solid ${border}`,
        padding: "6px 16px",
        fontSize: 13,
        display: "flex",
        alignItems: "center",
        gap: 12,
        flexShrink: 0,
      }}
    >
      <span aria-hidden="true">⚠</span>
      <span style={{ flex: 1 }}>{t("app.data_stale.banner", { when: ageLabel })}</span>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label={t("app.data_stale.dismiss_aria")}
        style={{
          background: "transparent",
          border: "none",
          color: fg,
          cursor: "pointer",
          fontSize: 16,
          lineHeight: 1,
          padding: "2px 6px",
        }}
      >
        ×
      </button>
    </div>
  );
}
