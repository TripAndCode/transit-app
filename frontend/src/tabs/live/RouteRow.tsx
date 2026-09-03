/**
 * One clickable triage row in the 最新観測 list: route name, severity dot,
 * deviation-from-baseline label, baseline→today comparison, and sample count.
 * Low-confidence routes (thin today sample) render muted with a 要確認
 * marker; a route whose baseline itself rests on few observations gets a
 * separate 平常値要確認 marker instead (its `low_confidence` flag may still
 * be false if today's own sample count is fine).
 */
import type { TFunction } from "i18next";
import { LOW_CONFIDENCE_SAMPLES, type RouteSummary } from "../../api/types";
import { delayColor } from "../../styles/tokens";
import { signedMin } from "./signedMin";

export function RouteRow({
  route,
  formatRoute,
  onOpen,
  t,
}: {
  route: RouteSummary;
  formatRoute: (rc: string) => string;
  onOpen: (route: RouteSummary) => void;
  t: TFunction;
}) {
  // The baseline itself can now rest on very few observations (agg_route_stats
  // no longer drops thin route/service groups at insert time) — independent of
  // `low_confidence`, which only judges today's sample count.
  const baselineLowConfidence =
    route.has_baseline && route.baseline_samples != null && route.baseline_samples < LOW_CONFIDENCE_SAMPLES;
  const dev = route.deviation_sec;
  const devLabel =
    dev == null
      ? t("live.row.no_baseline_note")
      : dev === 0
        ? t("live.row.deviation_flat")
        : t("live.row.deviation", { sign: dev > 0 ? "+" : "-", value: Math.abs(Math.round(dev / 60)) });

  return (
    <button
      type="button"
      onClick={() => onOpen(route)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        width: "100%",
        textAlign: "left",
        background: "transparent",
        color: "var(--text-primary)",
        border: "none",
        borderBottom: "1px solid var(--border-subtle)",
        padding: "8px 10px",
        cursor: "pointer",
        opacity: route.low_confidence ? 0.6 : 1,
      }}
    >
      <span style={{ flex: 1, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {formatRoute(route.route_code)}
      </span>
      <span
        aria-hidden="true"
        style={{ color: delayColor(route.avg_delay_sec / 60), fontSize: 10, lineHeight: 1 }}
      >
        ●
      </span>
      <span style={{ fontWeight: 700, minWidth: 88, textAlign: "right" }}>{devLabel}</span>
      {route.has_baseline && route.baseline_avg_sec != null && (
        <span style={{ fontSize: 12, color: "var(--text-tertiary)", minWidth: 150 }}>
          {t("live.row.baseline_today", {
            baseline: signedMin(route.baseline_avg_sec, t),
            today: signedMin(route.avg_delay_sec, t),
          })}
        </span>
      )}
      {route.low_confidence && (
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{t("live.row.low_confidence")}</span>
      )}
      {!route.low_confidence && baselineLowConfidence && (
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{t("live.row.low_confidence_baseline")}</span>
      )}
      {route.late5_pct != null && (
        <span style={{ fontSize: 11, color: "var(--text-tertiary)", minWidth: 72, textAlign: "right" }}>
          {t("live.row.late5_pct", { value: route.late5_pct.toFixed(1) })}
        </span>
      )}
      <span style={{ fontSize: 11, color: "var(--text-tertiary)", minWidth: 64, textAlign: "right" }}>
        {t("live.row.samples", { count: route.samples })}
      </span>
    </button>
  );
}
