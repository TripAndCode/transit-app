import type { TFunction } from "i18next";

/**
 * Single source of truth for map-tooltip HTML.
 *
 * Both the heatmap-circle click and the route-stop click feed a normalized
 * StopPopupData into renderStopPopupHTML so the popup template stays in
 * one place. Field rules:
 *
 * - stop_name is always shown as the primary heading
 * - platform_code renders as a "のりば N" badge next to the heading // i18n-ignore: JSDoc
 * - stop_code shows as a subtitle only when distinct from stop_name
 *   (Aomori's stop_name is often "<station> ②のりば" which already // i18n-ignore: JSDoc
 *   contains the stop_code, so duplicating it would be noisy)
 * - meta line ("停留所 #N ・ 系統 X") only renders when sequence or // i18n-ignore: JSDoc
 *   active_route is set; absent in plain heatmap mode
 * - contributing_routes lists the keito codes that contributed to a
 *   heatmap cluster's average — truncated to first 4 + "+N" overflow
 * - active_route is the route the user filtered to (route mode); shown
 *   instead of contributing_routes
 * - stop_id and period render small/grey at the bottom
 *
 * `t` is passed in from the React caller because this module is plain
 * TS (no hooks). All user-visible JP literals route through i18n keys
 * under `map.popup.*` + shared `common.unit_min` / `common.unit_count`.
 */

export type StopPopupData = {
  stop_name: string;
  stop_code?: string | null;
  platform_code?: string | null;
  /** Comma-joined list ("1_02,1_03") or single id. */
  stop_id?: string | null;
  /** Present in route mode (per-route stop sequence); absent in heatmap mode. */
  stop_sequence?: number | null;
  avg_min: number;
  samples: number;
  /** Heatmap mode: every route_code that contributed to this cluster. */
  contributing_routes?: string[];
  /** Route mode: the single route the user filtered to. */
  active_route?: string | null;
};

export type Period = { from: string; to: string };

export function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
  );
}

function poleBadgeHTML(platform_code: string | null | undefined, t: TFunction): string {
  const poles = (platform_code || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (poles.length === 0) return "";
  return (
    `<span style="display:inline-block;background:#eef0fa;color:#5b6cad;` +
    `border-radius:4px;padding:1px 6px;font-size:11px;margin-left:6px;` +
    `vertical-align:middle">${escapeHtml(t("map.popup.platform_label"))} ${escapeHtml(poles.join("/"))}</span>`
  );
}

function stopCodeSubtitleHTML(stop_code: string | null | undefined, stop_name: string): string {
  const sc = (stop_code || "").trim();
  if (!sc || sc === stop_name) return "";
  return `<div style="font-size:12px;color:#666;margin-top:1px">${escapeHtml(sc)}</div>`;
}

function metaLineHTML(
  seq: number | null | undefined,
  active_route: string | null | undefined,
  t: TFunction,
): string {
  const bits: string[] = [];
  if (typeof seq === "number") bits.push(`${escapeHtml(t("map.popup.stop_seq_prefix"))}${seq}`);
  if (active_route) bits.push(`${escapeHtml(t("map.popup.route_prefix"))} ${escapeHtml(active_route)}`);
  if (bits.length === 0) return "";
  return `<div style="color:#888;font-size:11px;margin-top:2px">${bits.join(" ・ ")}</div>`; // i18n-ignore: separator
}

function routesLineHTML(d: StopPopupData, t: TFunction): string {
  // Route mode wins — when the user has filtered to one route, listing
  // contributing_routes alongside would just repeat the active filter.
  if (d.active_route) return "";
  const routes = (d.contributing_routes || []).filter(Boolean);
  if (routes.length === 0) return "";
  const label =
    routes.length <= 4
      ? routes.join(", ")
      : `${routes.slice(0, 4).join(", ")} +${routes.length - 4}`;
  return `<br/>${escapeHtml(t("map.popup.routes_label"))} <span style="color:#555">${escapeHtml(label)}</span>`;
}

function stopIdLineHTML(stop_id: string | null | undefined): string {
  const ids = (stop_id || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (ids.length === 0) return "";
  const shown = ids.length <= 3 ? ids.join(", ") : `${ids.slice(0, 3).join(", ")} +${ids.length - 3}`;
  return (
    `<div style="font-size:11px;color:#888;margin-top:6px">` +
    `stop_id: <span style="font-family:ui-monospace,monospace">${escapeHtml(shown)}</span>` +
    `</div>`
  );
}

export function renderStopPopupHTML(d: StopPopupData, period: Period, t: TFunction): string {
  const samplesLabel = d.samples.toLocaleString("en-US");
  const unitMin = escapeHtml(t("common.unit_min"));
  const unitCount = escapeHtml(t("common.unit_count"));
  return (
    `<div style="font:13px sans-serif;min-width:220px">` +
    `<div><strong>${escapeHtml(d.stop_name)}</strong>${poleBadgeHTML(d.platform_code, t)}</div>` +
    stopCodeSubtitleHTML(d.stop_code, d.stop_name) +
    metaLineHTML(d.stop_sequence, d.active_route, t) +
    `<div style="margin-top:6px">` +
    `${escapeHtml(t("map.popup.avg_delay_label"))} ${d.avg_min.toFixed(1)}${unitMin}<br/>` +
    `${escapeHtml(t("map.popup.samples_label"))} ${samplesLabel}${unitCount}` +
    routesLineHTML(d, t) +
    `</div>` +
    stopIdLineHTML(d.stop_id) +
    `<div style="font-size:11px;color:#888;margin-top:4px">` +
    `${escapeHtml(t("map.popup.period_label"))} ${escapeHtml(period.from)} 〜 ${escapeHtml(period.to)}` +
    `</div>` +
    `</div>`
  );
}
