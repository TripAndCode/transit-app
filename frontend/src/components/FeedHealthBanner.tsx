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
 * Dismissible for the agency it is shown for, for as long as the page stays
 * loaded, like the staleness banner: a full reload shows it again.
 */
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../api/hooks";
import { useTapToExpandBanner } from "../hooks/useTapToExpandBanner";
import { useBannerDismissal } from "./bannerDismissalsContext";

export function FeedHealthBanner({ hidden = false }: { hidden?: boolean }) {
  const { agencyId: raw } = useParams();
  const agencyId = raw ? Number(raw) : null;
  const { t, i18n } = useTranslation();
  const { data } = useTodayRouteSummary(hidden ? null : agencyId, { autoRefresh: false });
  const [dismissed, dismiss] = useBannerDismissal("feed_health", agencyId);
  const { messageProps } = useTapToExpandBanner();

  if (hidden || agencyId == null) return null;
  const count = data?.clamp_count ?? 0;
  if (count <= 0) return null;
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
      <span {...messageProps}>{t("app.feed_health.banner", { count })}</span>
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
