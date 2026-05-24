import i18n from "../i18n";

// Locale-aware relative-time formatter. Reads the current i18n instance
// directly so non-React callers don't need to thread a `t`. Returns "—"
// for invalid or future-dated input (clock skew tolerant).
export function relativeTime(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!isFinite(diff) || diff < 0) return "—";
  if (diff < 60) return i18n.t("common.rel_seconds_ago", { count: Math.floor(diff) });
  if (diff < 3600) return i18n.t("common.rel_minutes_ago", { count: Math.floor(diff / 60) });
  if (diff < 86400) return i18n.t("common.rel_hours_ago", { count: Math.floor(diff / 3600) });
  return i18n.t("common.rel_days_ago", { count: Math.floor(diff / 86400) });
}
