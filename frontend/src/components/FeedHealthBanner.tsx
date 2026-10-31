/**
 * FeedHealthBanner — calm signal that a GTFS-RT feed emitted implausible
 * (frozen/stale) delay readings that were filtered out before averaging.
 *
 * Reads the same /today/route-summary endpoint as DataStalenessBanner (shared
 * cache, no extra request). The `clamp_count` is how many raw observations over
 * the last 7 analyzed days had an implausible |delay| (frozen/stale feed) and
 * were filtered out — a feed-health signal, distinct from the values shown
 * elsewhere (which already exclude these). A 7-day window is used (not just the
 * latest day) because these freezes recur across days. Renders only when that
 * count reaches FEED_HEALTH_BANNER_MIN_SHARE of the window's readings; a
 * smaller count is told in the top bar's freshness panel alone.
 *
 * Dismissible for the agency it is shown for, for as long as the page stays
 * loaded, like the staleness banner: a full reload shows it again.
 */
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../api/hooks";
import { feedHealthBannerDue } from "../api/feedHealth";
import { useBannerDismissal } from "./bannerDismissalsContext";

export function FeedHealthBanner({ hidden = false }: { hidden?: boolean }) {
  const { agencyId: raw } = useParams();
  const agencyId = raw ? Number(raw) : null;
  const { t, i18n } = useTranslation();
  const { data } = useTodayRouteSummary(hidden ? null : agencyId, { autoRefresh: false });
  const [dismissed, dismiss] = useBannerDismissal("feed_health", agencyId);

  if (hidden || agencyId == null) return null;
  const count = data?.clamp_count ?? 0;
  if (!feedHealthBannerDue(data?.raw_samples ?? 0, count)) return null;
  if (dismissed) return null;

  // Same calm warm-tan palette as DataStalenessBanner — never alarm red.
  const bg = "hsl(38, 50%, 95%)";
  const fg = "hsl(28, 45%, 32%)";
  const border = "hsl(38, 45%, 78%)";

  return (
    <div
      role="status"
      aria-live="polite"
      lang={i18n.language}
      data-shell-banner=""
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
      {/* Wraps rather than truncates on a phone: a cut-off notice reads as
          a broken one. */}
      <span style={{ flex: 1, minWidth: 0 }}>{t("app.feed_health.banner", { count })}</span>
      <button
        type="button"
        onClick={dismiss}
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
