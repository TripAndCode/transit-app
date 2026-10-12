import i18n from "../i18n";
import { CLOCK_SKEW_ALLOWANCE_MS } from "./clockSkew";

// Locale-aware relative-time formatter. Reads the current i18n instance
// directly so non-React callers don't need to thread a `t`. Returns "—"
// for invalid input or one dated beyond the clock-skew allowance; a time
// slightly ahead of `now` reads as "just now".
export function relativeTime(iso: string, now: number = Date.now()): string {
  const diff = (now - new Date(iso).getTime()) / 1000;
  if (!isFinite(diff) || diff * 1000 < CLOCK_SKEW_ALLOWANCE_MS) return "—";
  if (diff < 60) return i18n.t("common.rel_just_now");
  if (diff < 3600) return i18n.t("common.rel_minutes_ago", { count: Math.floor(diff / 60) });
  if (diff < 86400) return i18n.t("common.rel_hours_ago", { count: Math.floor(diff / 3600) });
  return i18n.t("common.rel_days_ago", { count: Math.floor(diff / 86400) });
}
