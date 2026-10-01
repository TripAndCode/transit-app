import type { Scope } from "../../api/scope";
import { dowValueLabel, serviceValueLabel, timeBandValueLabel, translationT, type LabelT } from "../../utils/filterValueLabels";

/** The `scope_applied` keys an endpoint reports, one per URL scope param. */
export type ScopeField = "from" | "to" | "dow" | "time_band" | "hour" | "service" | "routes" | "stop" | "dir" | "late" | "early";
export type TokenKey = "agency" | "routes" | "period" | "days" | "time" | "service" | "stop" | "dir" | "tolerance";
export type ScopeToken = { key: TokenKey; label: string; field: ScopeField | null };
type PhraseCtx = { t: LabelT; agencyName: string; routeLabel: (code: string) => string };

/** The on-time tolerance the server applies when `late` is unset. */
const DEFAULT_LATE_SEC = 60;

function dateLabel(iso: string, withYear: boolean): string {
  const [y, m, d] = iso.split("-").map(Number);
  return withYear ? `${y}/${m}/${d}` : `${m}/${d}`;
}

function toleranceLabel(sec: number, t: LabelT): string {
  if (sec < 60) return translationT(t, "scope.tolerance_sec", { n: sec });
  return translationT(t, "scope.tolerance_min", { n: Math.round((sec / 60) * 10) / 10 });
}

/** The scope as words, one token per condition, in sentence order. Each
 *  token names the `scope_applied` field that says whether a screen used it. */
export function scopeTokens(scope: Scope, { t, agencyName, routeLabel }: PhraseCtx): ScopeToken[] {
  const tr = (key: string, opts?: Record<string, unknown>) => translationT(t, key, opts);
  const crossesYear = scope.from.slice(0, 4) !== scope.to.slice(0, 4);
  const routes =
    scope.routes.length === 0
      ? tr("scope.routes_all")
      : scope.routes.length === 1
        ? routeLabel(scope.routes[0])
        : tr("scope.routes_count", { count: scope.routes.length });
  const tokens: ScopeToken[] = [
    { key: "agency", label: agencyName, field: null },
    { key: "routes", label: routes, field: "routes" },
    {
      key: "period",
      label: tr("scope.period", { from: dateLabel(scope.from, crossesYear), to: dateLabel(scope.to, crossesYear) }),
      field: "from",
    },
    { key: "days", label: scope.dow === "all" ? tr("scope.days_all") : dowValueLabel(scope.dow, t), field: "dow" },
    scope.hour
      ? { key: "time", label: tr("scope.hours", { from: scope.hour[0], to: scope.hour[1] }), field: "hour" }
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
