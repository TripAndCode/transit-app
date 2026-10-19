/**
 * DataStalenessBanner — calm warning when GTFS ingest hasn't run recently.
 *
 * Reads the same /today/route-summary endpoint the operations map uses
 * (shared cache, no extra request). When the latest observation is more than
 * STALE_THRESHOLD_HOURS old, render a soft warning pill above the main
 * content so users know they're looking at historical data even when the
 * rest of the UI reads as live.
 *
 * Dismissible for the agency it is shown for, for as long as the page stays
 * loaded; a full reload shows it again, so the signal is never permanently
 * hidden (see BannerDismissals).
 */
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../api/hooks";
import { useTapToExpandBanner } from "../hooks/useTapToExpandBanner";
import { useBannerDismissal } from "./bannerDismissalsContext";
import { useNow } from "../hooks/useNow";

const STALE_THRESHOLD_HOURS = 24;
/** The banner sits in the app shell and its query does not poll, so the feed's
 *  age is re-read on this clock: a page left open warns within a minute of the
 *  threshold passing. */
const AGE_RECHECK_MS = 60_000;

function relativeAgeHours(iso: string, now: Date): number {
  const captured = new Date(iso).getTime();
  if (!Number.isFinite(captured)) return 0;
  return (now.getTime() - captured) / (1000 * 60 * 60);
}

export function DataStalenessBanner({ hidden = false }: { hidden?: boolean }) {
  const { agencyId: raw } = useParams();
  const agencyId = raw ? Number(raw) : null;
  const { t, i18n } = useTranslation();
  const { data } = useTodayRouteSummary(hidden ? null : agencyId, { autoRefresh: false });
  const now = useNow(AGE_RECHECK_MS);
  const [dismissed, dismiss] = useBannerDismissal("data_stale", agencyId);
  const { messageProps } = useTapToExpandBanner();

  if (hidden || agencyId == null) return null;
  const captured = data?.latest_captured_at;
  if (!captured) return null;
  const ageH = relativeAgeHours(captured, now);
  if (ageH < STALE_THRESHOLD_HOURS) return null;
  if (dismissed) return null;

  const days = Math.floor(ageH / 24);
  const ageLabel =
    days >= 1
      ? t("common.rel_days_ago", { count: days })
      : t("common.rel_hours_ago", { count: Math.floor(ageH) });


  // Choose a calm warm-tan color (max saturation 50%) — never alarm red.
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
      <span {...messageProps}>{t("app.data_stale.banner", { when: ageLabel })}</span>
      <button
        type="button"
        onClick={dismiss}
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
