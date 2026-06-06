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
  /** Emoji shown next to title. */
  emoji: string;
  /** Tool slug to dispatch (e.g. "top_n", "trend"). */
  tool: string;
  /** Static args merged into the final args (e.g. {"metric": "avg_delay"} when not user-selectable). */
  fixed_args?: Record<string, unknown>;
  /** Parameter inputs to render. */
  params: ParamSpec[];
  /** Renders the user_summary preview string from current values. */
  buildSummary: (values: Record<string, unknown>, t: TFunction) => string;
};

/**
 * Builds the 5 parameterized question card templates for the Ask dashboard.
 * Each `buildSummary` closure receives `t` at call-site (from the card
 * component), so the template list is locale-independent and can be
 * memoized once per language change.
 */
export function buildCardTemplates(): CardTemplate[] {
  return [
    {
      id: "top_delay",
      title_key: "ask.card.top_delay.title",
      emoji: "🏆",
      tool: "top_n",
      fixed_args: { metric: "avg_delay" },
      params: [
        { kind: "limit", name: "k", min: 3, max: 20, default: 5 },
        { kind: "service", name: "service_type", default: "all" },
      ],
      buildSummary: (v, t) =>
        t("ask.card.top_delay.summary", {
          k: v.k ?? 5,
          service: t(
            `ask.card.param.service.${(v.service_type as string) ?? "all"}`,
          ),
        }),
    },
    {
      id: "ontime_rank",
      title_key: "ask.card.ontime_rank.title",
      emoji: "🎯",
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
        t("ask.card.ontime_rank.summary", {
          k: v.k ?? 5,
          dir:
            v.best_first === "true"
              ? t("ask.card.ontime_rank.best")
              : t("ask.card.ontime_rank.worst"),
        }),
    },
    {
      id: "route_trend",
      title_key: "ask.card.route_trend.title",
      emoji: "📈",
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
      emoji: "⚖️",
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
      emoji: "🚏",
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
