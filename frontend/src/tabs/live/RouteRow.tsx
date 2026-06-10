import type { TFunction } from "i18next";
import type { RouteSummary } from "../../api/types";
import { delayColor } from "../../styles/tokens";

function minStr(sec: number): string {
  return Math.round(sec / 60).toString();
}

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
            baseline: `+${minStr(route.baseline_avg_sec)}`,
            today: `+${minStr(route.avg_delay_sec)}`,
          })}
        </span>
      )}
      {route.low_confidence && (
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{t("live.row.low_confidence")}</span>
      )}
      <span style={{ fontSize: 11, color: "var(--text-tertiary)", minWidth: 64, textAlign: "right" }}>
        {t("live.row.samples", { count: route.samples })}
      </span>
    </button>
  );
}
