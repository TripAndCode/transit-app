import type { Scope } from "../../api/scope";
import { dowValueLabel, serviceValueLabel, timeBandValueLabel, translationT, type LabelT } from "../../utils/filterValueLabels";

/** The `scope_applied` keys an endpoint reports, one per URL scope param. */
export type ScopeField = "from" | "to" | "dow" | "time_band" | "hour" | "service" | "routes" | "stop" | "dir" | "late" | "early";
export type TokenKey = "agency" | "routes" | "period" | "days" | "time" | "service" | "stop" | "dir" | "early" | "tolerance";
export type ScopeToken = { key: TokenKey; label: string; field: ScopeField | null };
type PhraseCtx = {
  t: LabelT;
  agencyName: string;
  routeLabel: (code: string) => string;
  /** The line a route code belongs to, as the route picker groups them. */
  routeGroup?: (code: string) => string | undefined;
};

/** The on-time tolerance the server applies when `late` is unset. */
const DEFAULT_LATE_SEC = 60;

function dateLabel(iso: string, withYear: boolean): string {
  const [y, m, d] = iso.split("-").map(Number);
  return withYear ? `${y}/${m}/${d}` : `${m}/${d}`;
}

function secondsLabel(sec: number, t: LabelT, minKey: string, secKey: string): string {
  if (sec < 60) return translationT(t, secKey, { n: sec });
  return translationT(t, minKey, { n: Math.round((sec / 60) * 10) / 10 });
}

export function toleranceLabel(sec: number, t: LabelT): string {
  return secondsLabel(sec, t, "scope.tolerance_min", "scope.tolerance_sec");
}

/** The scope as words, one token per condition, in sentence order. Each
 *  token names the `scope_applied` field that says whether a screen used it. */
function routesLabel(routes: string[], { t, routeLabel, routeGroup }: PhraseCtx): string {
  if (routes.length === 0) return translationT(t, "scope.routes_all");
  if (routes.length === 1) return routeLabel(routes[0]);
  // A line picked by name selects every variant of it; say the line.
  const group = routeGroup?.(routes[0]);
  if (group && routes.every((code) => routeGroup?.(code) === group)) {
    return `${group} ${translationT(t, "filters.routes.variant_count", { count: routes.length })}`;
  }
  return translationT(t, "scope.routes_count", { count: routes.length });
}

function daysLabel(dow: Scope["dow"], t: LabelT): string {
  if (dow === "all") return translationT(t, "scope.days_all");
  if (dow === "weekday") return translationT(t, "scope.days_weekday");
  if (dow === "weekend") return translationT(t, "scope.days_weekend");
  return dowValueLabel(dow, t);
}

function hoursLabel([from, to]: [number, number], t: LabelT): string {
  return from === to ? translationT(t, "scope.hour_single", { h: from }) : translationT(t, "scope.hours", { from, to });
}

export function scopeTokens(scope: Scope, ctx: PhraseCtx): ScopeToken[] {
  const { t, agencyName } = ctx;
  const tr = (key: string, opts?: Record<string, unknown>) => translationT(t, key, opts);
  const crossesYear = scope.from.slice(0, 4) !== scope.to.slice(0, 4);
  const routes = routesLabel(scope.routes, ctx);
  const tokens: ScopeToken[] = [
    { key: "agency", label: agencyName, field: null },
    { key: "routes", label: routes, field: "routes" },
    {
      key: "period",
      label: tr("scope.period", { from: dateLabel(scope.from, crossesYear), to: dateLabel(scope.to, crossesYear) }),
      field: "from",
    },
    { key: "days", label: daysLabel(scope.dow, t), field: "dow" },
    scope.hour
      ? { key: "time", label: hoursLabel(scope.hour, t), field: "hour" }
      : {
          key: "time",
          label: scope.time_band === "all" ? tr("scope.time_all") : timeBandValueLabel(scope.time_band, t),
          field: "time_band",
        },
  ];
  if (scope.service !== "all") {
    tokens.push({ key: "service", label: tr("scope.service", { value: serviceValueLabel(scope.service, t) }), field: "service" });
  }
  if (scope.stop) tokens.push({ key: "stop", label: tr("scope.stop", { id: scope.stop }), field: "stop" });
  if (scope.dir != null) tokens.push({ key: "dir", label: tr("scope.dir", { dir: scope.dir }), field: "dir" });
  if (scope.early != null) {
    tokens.push({ key: "early", label: secondsLabel(scope.early, t, "scope.early_min", "scope.early_sec"), field: "early" });
  }
  tokens.push({ key: "tolerance", label: toleranceLabel(scope.late ?? DEFAULT_LATE_SEC, t), field: "late" });
  return tokens;
}

/** A locale template's text runs and `[slot]` placeholders, in order. Square
 *  brackets rather than `{{ }}` so i18next leaves the slots alone. */
export function sentenceParts(template: string): ({ text: string } | { slot: string })[] {
  const parts: ({ text: string } | { slot: string })[] = [];
  let last = 0;
  for (const m of template.matchAll(/\[(\w+)\]/g)) {
    if (m.index > last) parts.push({ text: template.slice(last, m.index) });
    parts.push({ slot: m[1] });
    last = m.index + m[0].length;
  }
  if (last < template.length) parts.push({ text: template.slice(last) });
  return parts;
}

/** The scope's conditions as one line, for saved-analysis titles. */
export function scopeTitle(scope: Scope, ctx: PhraseCtx): string {
  return scopeTokens(scope, ctx)
    .filter((tok) => tok.key !== "agency")
    .map((tok) => tok.label)
    .join(translationT(ctx.t, "common.list_separator"));
}
