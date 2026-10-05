import { Bus, Clock, MapPin, Rows3, Scale, Target, TrendingUp, type LucideIcon } from "lucide-react";
import type { TFunction } from "i18next";

export type ParamSpec =
  | { kind: "route"; name: string; required?: boolean }
  | {
      kind: "metric";
      name: string;
      options: Array<{ value: string; label_key: string }>;
      default?: string;
    }
  | { kind: "limit"; name: string; min?: number; max?: number; default?: number }
  | { kind: "granularity"; name: string; default?: "day" | "week" | "month" }
  | { kind: "service"; name: string; default?: "all" | "weekday" | "weekend" };

export type CardTemplate = {
  /** Unique slug, used in user_summary fallback. */
  id: string;
  /** i18n key under `ask.card.<id>.title` */
  title_key: string;
  /** A line icon from the app's set, shown before the card's label. */
  icon: LucideIcon;
  /** Tool slug to dispatch (e.g. "top_n", "trend"). */
  tool: string;
  /** Static args merged into the final args (e.g. {"metric": "avg_delay"} when not user-selectable). */
  fixed_args?: Record<string, unknown>;
  /** Parameter inputs to render. */
  params: ParamSpec[];
  /** Renders the user_summary preview string from current values. */
  buildSummary: (values: Record<string, unknown>, t: TFunction) => string;
  /** i18n key for an illustrative example-answer line shown under the
   *  question on the landing card (instant-run templates only — a static
   *  example wouldn't make sense before a route is picked on the
   *  route-required templates). Always explicitly "e.g."-framed, never
   *  phrased as live data. */
  example_answer_key?: string;
};

/**
 * Builds the parameterized question card templates for the Ask dashboard.
 * Each `buildSummary` closure receives `t` at call-site (from the card
 * component), so the template list itself is locale-independent — safe to
 * call directly on every render (see `QuestionDock.tsx`/`AskTab.tsx`);
 * translation happens later via `t()`, not here.
 */
export function buildCardTemplates(): CardTemplate[] {
  return [
    {
      id: "stop_patterns",
      title_key: "ask.card.stop_patterns.title",
      icon: Rows3,
      tool: "route_stop_patterns",
      params: [{ kind: "route", name: "route", required: true }],
      buildSummary: (v, t) => v.route ? t("ask.card.stop_patterns.summary", { route: v.route }) : t("ask.card.stop_patterns.title"),
    },
    {
      id: "stop_hotspots",
      title_key: "ask.card.stop_hotspots.title",
      icon: MapPin,
      tool: "segment_hotspots",
      params: [{ kind: "route", name: "route", required: true }],
      buildSummary: (v, t) => v.route
        ? t("ask.card.stop_hotspots.summary", { route: v.route })
        : t("ask.card.stop_hotspots.title"),
    },
    {
      id: "top_delay",
      title_key: "ask.card.top_delay.title",
      icon: Clock,
      tool: "top_n",
      fixed_args: { metric: "avg_delay" },
      params: [
        { kind: "limit", name: "k", min: 3, max: 20, default: 5 },
        { kind: "service", name: "service_type", default: "all" },
      ],
      buildSummary: (v, t) =>
        t(`ask.card.top_delay.summary_${(v.service_type as string) ?? "all"}`, { k: v.k ?? 5 }),
      example_answer_key: "ask.card.top_delay.example_answer",
    },
    {
      id: "ontime_rank",
      title_key: "ask.card.ontime_rank.title",
      icon: Target,
      tool: "on_time",
      fixed_args: {},
      params: [
        { kind: "limit", name: "k", min: 3, max: 20, default: 5 },
        {
          kind: "metric",
          name: "best_first",
          options: [
            { value: "true", label_key: "ask.card.ontime_rank.best" },
            { value: "false", label_key: "ask.card.ontime_rank.worst" },
          ],
          default: "false",
        },
      ],
      buildSummary: (v, t) =>
        t(v.best_first === "true" ? "ask.card.ontime_rank.summary_best" : "ask.card.ontime_rank.summary_worst", {
          k: v.k ?? 5,
        }),
      example_answer_key: "ask.card.ontime_rank.example_answer",
    },
    {
      id: "route_trend",
      title_key: "ask.card.route_trend.title",
      icon: TrendingUp,
      tool: "trend",
      fixed_args: { metric: "avg_delay" },
      params: [
        { kind: "route", name: "route_code", required: true },
        { kind: "granularity", name: "granularity", default: "week" },
      ],
      buildSummary: (v, t) =>
        v.route_code
          ? t("ask.card.route_trend.summary", {
              route: v.route_code,
              gran: t(
                `ask.card.param.granularity.${(v.granularity as string) ?? "week"}`,
              ),
            })
          : t("ask.card.route_trend.placeholder"),
    },
    {
      id: "weekday_vs_weekend",
      title_key: "ask.card.weekday_vs_weekend.title",
      icon: Scale,
      tool: "cmp_service",
      fixed_args: { metric: "avg_delay" },
      params: [{ kind: "route", name: "route_code", required: true }],
      buildSummary: (v, t) =>
        v.route_code
          ? t("ask.card.weekday_vs_weekend.summary", { route: v.route_code })
          : t("ask.card.weekday_vs_weekend.placeholder"),
    },
    {
      id: "route_overview",
      title_key: "ask.card.route_overview.title",
      icon: Bus,
      tool: "route_stats",
      fixed_args: {},
      params: [{ kind: "route", name: "route_code", required: true }],
      buildSummary: (v, t) =>
        v.route_code
          ? t("ask.card.route_overview.summary", { route: v.route_code })
          : t("ask.card.route_overview.placeholder"),
    },
  ];
}

export function defaultsFor(tpl: CardTemplate): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of tpl.params) {
    if (p.kind === "limit") out[p.name] = p.default ?? 5;
    else if (p.kind === "service") out[p.name] = p.default ?? "all";
    else if (p.kind === "granularity") out[p.name] = p.default ?? "week";
    else if (p.kind === "metric") out[p.name] = p.default ?? p.options[0].value;
    // route stays unset (null) — required check will surface it
  }
  return out;
}

/** True if this template requires a route pick before it can dispatch —
 *  used to split templates into instant-run cards vs. route-picker pills
 *  on the Ask tab's landing state. */
export function needsRoute(tpl: CardTemplate): boolean {
  return tpl.params.some((p) => p.kind === "route" && p.required);
}
