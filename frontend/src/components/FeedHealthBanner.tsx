/**
 * FeedHealthBanner — calm signal that a GTFS-RT feed emitted implausible
 * (frozen/stale) delay readings that were filtered out before averaging.
 *
 * Reads the same /today/route-summary endpoint as DataStalenessBanner (shared
 * cache, no extra request). The `clamp_count` is how many raw observations over
 * the last 7 analyzed days had an implausible |delay| (frozen/stale feed) and
 * were filtered out — a feed-health signal, distinct from the values shown
 * elsewhere (which already exclude these). A 7-day window is used (not just the
 * latest day) because these freezes recur across days. Renders only when
 * something was actually filtered.
 *
 * Dismissible per session — re-appears on full page reload, like the staleness
 * banner, so the signal is never permanently hidden.
 */
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../api/hooks";

const SESSION_DISMISS_KEY = "fh_banner_dismissed_at";

export function FeedHealthBanner() {
  const { agencyId: raw } = useParams();
  const agencyId = raw ? Number(raw) : null;
  const { t, i18n } = useTranslation();
  const { data } = useTodayRouteSummary(agencyId, { autoRefresh: false });
  const [dismissed, setDismissed] = useState(
    () => sessionStorage.getItem(SESSION_DISMISS_KEY) != null,
  );

  if (agencyId == null) return null;
  const count = data?.clamp_count ?? 0;
  if (count <= 0) return null;
  if (dismissed) return null;

  function handleDismiss() {
    sessionStorage.setItem(SESSION_DISMISS_KEY, String(Date.now()));
    setDismissed(true);
  }

  // Same calm warm-tan palette as DataStalenessBanner — never alarm red.
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
      <span style={{ flex: 1 }}>{t("app.feed_health.banner", { count })}</span>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label={t("app.feed_health.dismiss_aria")}
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
