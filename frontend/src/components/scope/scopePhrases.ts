import { DEFAULT_WINDOW, EARLY_ANY, type OnTimeWindow } from "../../api/onTimeDefault";
import type { Scope } from "../../api/scope";
import { dowValueLabel, serviceValueLabel, timeBandValueLabel, translationT, type LabelT } from "../../utils/filterValueLabels";
import { roundTenth } from "../../utils/format";

/** The `scope_applied` keys an endpoint reports, one per URL scope param. */
export type ScopeField = "from" | "to" | "dow" | "time_band" | "hour" | "service" | "routes" | "stop" | "dir" | "late" | "early";
export type TokenKey = "agency" | "routes" | "period" | "days" | "time" | "service" | "stop" | "dir" | "early" | "tolerance";
export type ScopeToken = { key: TokenKey; label: string; field: ScopeField | null };
export type PhraseCtx = {
  t: LabelT;
  agencyName: string;
  routeLabel: (code: string) => string;
  /** The line a route code belongs to, as the route picker groups them. */
  routeGroup?: (code: string) => string | undefined;
  /** A route code's plate text (its number/letter badge), for the routes
   *  token's decorative plate stack. */
  routePlate?: (code: string) => string | null;
};

const DEFAULT_LATE_SEC = DEFAULT_WINDOW.late_sec;

function dateLabel(iso: string, withYear: boolean): string {
  const [y, m, d] = iso.split("-").map(Number);
  return withYear ? `${y}/${m}/${d}` : `${m}/${d}`;
}

// Every span a phrase names goes through here, whole seconds under a minute
// and else minutes to a tenth, so one edge prints one way wherever it shows.
function secondsLabel(sec: number, t: LabelT, minKey: string, secKey: string): string {
  if (sec < 60) return translationT(t, secKey, { n: sec });
  return translationT(t, minKey, { n: roundTenth(sec / 60) });
}

/** A span alone, as a figure and its unit. */
export function durationLabel(sec: number, t: LabelT): string {
  return secondsLabel(sec, t, "scope.duration_min", "scope.duration_sec");
}

export function toleranceLabel(sec: number, t: LabelT): string {
  return secondsLabel(sec, t, "scope.tolerance_min", "scope.tolerance_sec");
}

export function earlyLabel(sec: number, t: LabelT): string {
  return secondsLabel(sec, t, "scope.early_min", "scope.early_sec");
}

/** An on-time window's two sides in the scope sentence's words. */
export function windowLabels({ early_sec, late_sec }: OnTimeWindow, t: LabelT): { late: string; early: string } {
  return {
    late: toleranceLabel(late_sec, t),
    early: early_sec == null ? translationT(t, "scope.early_any") : earlyLabel(early_sec, t),
  };
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

/** The routes token's decorative plate stack: one plate per picked line (its
 *  first-selected variant), or, when every pick is one line, that line's own
 *  distinct variant plates -- up to three either way. Across several lines,
 *  a line past the third or one with no plate of its own folds into a "+N"
 *  count, so the stack never shows fewer lines than were picked. A single
 *  line shows only the plates its variants have, possibly none, since the
 *  label already names that line. Purely visual: `routesLabel`'s own text
 *  is unaffected and stays the sentence's only accessible description. */
export function routeTokenPlates(routes: string[], ctx: PhraseCtx): { plates: string[]; more: number } {
  if (routes.length === 0) return { plates: [], more: 0 };
  const plateOf = (code: string): string | null => (ctx.routePlate ? ctx.routePlate(code) : code);
  const groupOf = (code: string) => ctx.routeGroup?.(code) ?? code;
  const singleGroup = new Set(routes.map(groupOf)).size === 1;
  const sourceCodes: string[] = [];
  const seenGroups = new Set<string>();
  for (const code of routes) {
    const g = groupOf(code);
    if (!singleGroup && seenGroups.has(g)) continue;
    seenGroups.add(g);
    sourceCodes.push(code);
  }
  const texts: string[] = [];
  let folded = 0;
  for (const code of sourceCodes) {
    const text = plateOf(code);
    if (text && !texts.includes(text)) texts.push(text);
    else if (!singleGroup) folded += 1;
  }
  const total = texts.length + folded;
  const plates = total <= 3 ? texts : texts.slice(0, 2);
  return { plates, more: total - plates.length };
}

export function daysLabel(dow: Scope["dow"], t: LabelT): string {
  if (dow === "all") return translationT(t, "scope.days_all");
  if (dow === "weekday") return translationT(t, "scope.days_weekday");
  if (dow === "weekend") return translationT(t, "scope.days_weekend");
  return dowValueLabel(dow, t);
}

function hoursLabel([from, to]: [number, number], t: LabelT): string {
  return from === to ? translationT(t, "scope.hour_single", { h: from }) : translationT(t, "scope.hours", { from, to });
}

/** The period in words, with years only when it crosses one. */
export function periodLabel(scope: Pick<Scope, "from" | "to">, t: LabelT): string {
  const crossesYear = scope.from.slice(0, 4) !== scope.to.slice(0, 4);
  return translationT(t, "scope.period", { from: dateLabel(scope.from, crossesYear), to: dateLabel(scope.to, crossesYear) });
}

export function scopeTokens(scope: Scope, ctx: PhraseCtx): ScopeToken[] {
  const { t, agencyName } = ctx;
  const tr = (key: string, opts?: Record<string, unknown>) => translationT(t, key, opts);
  const routes = routesLabel(scope.routes, ctx);
  const tokens: ScopeToken[] = [
    { key: "agency", label: agencyName, field: null },
    { key: "routes", label: routes, field: "routes" },
    { key: "period", label: periodLabel(scope, t), field: "from" },
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
    const label = scope.early === EARLY_ANY ? tr("scope.early_any") : earlyLabel(scope.early, t);
    tokens.push({ key: "early", label, field: "early" });
  }
  tokens.push({ key: "tolerance", label: toleranceLabel(scope.late ?? DEFAULT_LATE_SEC, t), field: "late" });
  return tokens;
}

/** `scope_applied` as an endpoint reports it, keyed by URL param name
 *  (api/scope_applied.py). */
export type ScopeApplied = Partial<Record<ScopeField | (string & {}), boolean>> | null | undefined;

/** Whether a condition narrows anything. A condition at its default filters
 *  nothing, so a screen ignoring it is not worth striking through. */
function conditionIsSet(key: TokenKey, scope: Scope): boolean {
  switch (key) {
    case "agency":
      return false;
    case "routes":
      return scope.routes.length > 0;
    case "days":
      return scope.dow !== "all";
    case "time":
      return scope.hour != null || scope.time_band !== "all";
    case "tolerance":
      return scope.late != null;
    default:
      return true;
  }
}

/** Whether a token's condition is set but the screen's data did not use it
 *  (`applied[field] === false`), so the sentence strikes it through. An
 *  absent `applied` means every condition was used. */
export function isScopeConditionOff(token: ScopeToken, scope: Scope, applied: ScopeApplied): boolean {
  return token.field != null && applied?.[token.field] === false && conditionIsSet(token.key, scope);
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

/** The period's length in days, both ends included. */
export function periodDays({ from, to }: Pick<Scope, "from" | "to">): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1;
}

/** Whether a condition differs from what a reset gives back. The optional
 *  conditions are tokens only while set, so they always differ. A null
 *  `defaultPeriod` means it is not known yet, and the period counts as
 *  unchanged rather than flashing as changed until it is. */
export function tokenChanged(key: TokenKey, scope: Scope, defaultPeriod: Pick<Scope, "from" | "to"> | null): boolean {
  switch (key) {
    case "agency":
      return false;
    case "routes":
      return scope.routes.length > 0;
    case "period":
      return defaultPeriod != null && (scope.from !== defaultPeriod.from || scope.to !== defaultPeriod.to);
    case "days":
      return scope.dow !== "all";
    case "time":
      return scope.hour != null || scope.time_band !== "all";
    case "tolerance":
      return (scope.late ?? DEFAULT_LATE_SEC) !== DEFAULT_LATE_SEC;
    default:
      return true;
  }
}

/** A name to offer when saving `scope` as a view: the conditions it changes,
 *  or its routes and period when it changes none. */
export function suggestedViewName(scope: Scope, ctx: PhraseCtx, defaultPeriod: Pick<Scope, "from" | "to"> | null): string {
  const tokens = scopeTokens(scope, ctx);
  const changed = tokens.filter((tok) => tokenChanged(tok.key, scope, defaultPeriod));
  const picked = changed.length > 0 ? changed : tokens.filter((tok) => tok.key === "routes" || tok.key === "period");
  return picked.map((tok) => tok.label).join(translationT(ctx.t, "common.list_separator"));
}

/** The scope's conditions as one line, for saved-analysis titles. */
export function scopeTitle(scope: Scope, ctx: PhraseCtx): string {
  return scopeTokens(scope, ctx)
    .filter((tok) => tok.key !== "agency")
    .map((tok) => tok.label)
    .join(translationT(ctx.t, "common.list_separator"));
}
