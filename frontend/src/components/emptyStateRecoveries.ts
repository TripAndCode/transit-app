import type { TFunction } from "i18next";
import type { Recovery } from "./EmptyState";

/** The subset of RangeCtx that explains an empty result — kept structural
 *  (not `import type { RangeCtx }`) so this module works for any caller that
 *  shapes its own filter state the same way. */
type ReasonCtx = {
  dow?: string;
  time_band?: string;
  service?: string;
  routes?: string[];
};

// Every key this module reads lives in the main `translation` resource
// (ja.json/en.json), never in a feature namespace like "design" -- callers
// bound to a different default namespace (e.g. AnalysisTab/ReportsHomeTab's
// `useTranslation("design")`) must still resolve the real copy, not the
// bare key string. `{ ns: "translation" }` on every lookup makes that
// explicit regardless of the caller's own default.
function tt(t: TFunction, key: string, options?: Record<string, unknown>): string {
  return t(key, { ns: "translation", ...options });
}

/** Renders a RangeCtx `service` value ("平日" / "土日祝" / "all") for display.
 *  Exported for reuse anywhere else a stored ctx needs the same rendering
 *  (e.g. the Ask evidence card's provenance disclosure) -- one canonical
 *  mapping from the query-contract value to display text. */
export function serviceValueLabel(service: string, t: TFunction): string {
  if (service === "平日") return tt(t, "filters.service.weekday"); // i18n-ignore: query contract
  if (service === "土日祝") return tt(t, "filters.service.weekend"); // i18n-ignore: query contract
  return service;
}

export function dowValueLabel(dow: string, t: TFunction): string {
  if (dow === "weekday") return tt(t, "filters.dow.weekday");
  if (dow === "weekend") return tt(t, "filters.dow.weekend");
  return dow;
}

export function timeBandValueLabel(timeBand: string, t: TFunction): string {
  const key = `filters.time_band.${timeBand}`;
  const label = tt(t, key);
  return label === key ? timeBand : label;
}

/** Short statements of which non-default filter dimensions are currently
 *  active, in the order they'd narrow a query: days, time, service, routes.
 *  Purely descriptive -- pair with {@link buildFilterCtxRecoveries} for the
 *  matching "do something about it" actions. */
export function buildFilterCtxReasons(ctx: ReasonCtx, t: TFunction): string[] {
  const reasons: string[] = [];
  if (ctx.dow && ctx.dow !== "all") {
    reasons.push(tt(t, "empty_state.reason_format", { label: tt(t, "filters.dow.label"), value: dowValueLabel(ctx.dow, t) }));
  }
  if (ctx.time_band && ctx.time_band !== "all") {
    reasons.push(
      tt(t, "empty_state.reason_format", {
        label: tt(t, "filters.time_band.label"),
        value: timeBandValueLabel(ctx.time_band, t),
      }),
    );
  }
  if (ctx.service && ctx.service !== "all") {
    reasons.push(
      tt(t, "empty_state.reason_format", { label: tt(t, "filters.service.label"), value: serviceValueLabel(ctx.service, t) }),
    );
  }
  if (ctx.routes && ctx.routes.length > 0) {
    reasons.push(tt(t, "empty_state.reason_format", { label: tt(t, "filters.routes.label"), value: ctx.routes.join(", ") }));
  }
  return reasons;
}

/** The three ctx-driven recoveries an EmptyState can offer, in order of
 *  narrowest-to-widest fix: drop the route filter, reset service type, then
 *  jump to a window with real data. Each is included only when the ctx it
 *  would reset is actually non-default (or, for the date jump, only when the
 *  caller has a jump target at all) -- never render a "clear" button next
 *  to a filter that's already cleared. */
export function buildFilterCtxRecoveries({
  ctx,
  onClearRoutes,
  onResetService,
  jumpToLatestData,
  t,
}: {
  ctx: Pick<ReasonCtx, "service" | "routes">;
  onClearRoutes: () => void;
  onResetService: () => void;
  jumpToLatestData: (() => void) | null;
  t: TFunction;
}): Recovery[] {
  const recoveries: Recovery[] = [];
  if (ctx.routes && ctx.routes.length > 0) {
    recoveries.push({ label: tt(t, "empty_state.recovery.clear_routes"), onClick: onClearRoutes });
  }
  if (ctx.service && ctx.service !== "all") {
    recoveries.push({ label: tt(t, "empty_state.recovery.reset_service"), onClick: onResetService });
  }
  if (jumpToLatestData) {
    recoveries.push({ label: tt(t, "empty_state.recovery.jump_latest_data"), onClick: jumpToLatestData });
  }
  return recoveries;
}
