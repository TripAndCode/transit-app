import type { FollowupChip } from "../api/types";

type TemplateBuilder = (args: Record<string, unknown>, firstRow: unknown[] | null) =>
  { tool: string; args: Record<string, unknown> } | null;

type Template = {
  id: string;
  title_ja: string;
  title_en: string;
  build: TemplateBuilder;
};

const stripDates = (args: Record<string, unknown>) =>
  Object.fromEntries(Object.entries(args).filter(([k]) => k !== "from_date" && k !== "to_date"));

const tw = (args: Record<string, unknown>, t: string) =>
  ({ ...stripDates(args), time_window: t });

const flipBestFirst: TemplateBuilder = (args) => ({
  tool: "top_n",
  args: { ...args, best_first: !Boolean(args.best_first), n: 5 },
});

const TEMPLATES: Record<string, Template[]> = {
  top_n: [
    { id: "opposite", title_ja: "↕ 逆順に切り替え",   title_en: "↕ Flip ranking",        build: flipBestFirst },
    { id: "tw-2weeks", title_ja: "📅 直近2週間に変更", title_en: "📅 Last 2 weeks",       build: (a) => ({ tool: "top_n", args: tw(a, "last_2_weeks") }) },
    { id: "tw-30days", title_ja: "📅 直近30日に変更",  title_en: "📅 Last 30 days",       build: (a) => ({ tool: "top_n", args: tw(a, "last_30_days") }) },
    { id: "compare-dow", title_ja: "⚖️ 平日/土日で比較", title_en: "⚖️ Compare weekday vs weekend", build: () => ({ tool: "compare_segments", args: { dimension: "dow" } }) },
  ],
  time_series: [
    { id: "gran-day",  title_ja: "📅 日別に切り替え", title_en: "📅 Daily",        build: (a) => ({ tool: "time_series", args: { ...a, granularity: "day" } }) },
    { id: "gran-week", title_ja: "📅 週別に切り替え", title_en: "📅 Weekly",       build: (a) => ({ tool: "time_series", args: { ...a, granularity: "week" } }) },
    { id: "tw-2weeks", title_ja: "📆 直近2週間に絞る", title_en: "📆 Last 2 weeks", build: (a) => ({ tool: "time_series", args: tw(a, "last_2_weeks") }) },
    { id: "compare-dow", title_ja: "⚖️ 平日/土日で比較", title_en: "⚖️ Compare", build: () => ({ tool: "compare_segments", args: { dimension: "dow" } }) },
  ],
  compare_segments: [
    { id: "by-service", title_ja: "⚖️ 便種別で比較", title_en: "⚖️ Compare by service", build: () => ({ tool: "compare_segments", args: { dimension: "service_type" } }) },
    { id: "rank-top",   title_ja: "🏆 ランキングを見る", title_en: "🏆 See ranking",     build: () => ({ tool: "top_n", args: { metric: "avg_delay", n: 10 } }) },
  ],
  route_stats: [
    { id: "route-trend",  title_ja: "📈 この路線の時系列", title_en: "📈 Trend for this route", build: (a) => ({ tool: "time_series", args: Object.fromEntries(Object.entries(a).filter(([k]) => k.startsWith("route"))) }) },
    { id: "compare-dow",  title_ja: "⚖️ 平日/土日で比較", title_en: "⚖️ Compare",              build: () => ({ tool: "compare_segments", args: { dimension: "dow" } }) },
  ],
  describe_data: [
    { id: "next-page",  title_ja: "▸ 次の50件", title_en: "▸ Next 50", build: (a) => ({ tool: "describe_data", args: { ...a, offset: (Number(a.offset) || 0) + (Number(a.limit) || 50) } }) },
    { id: "date-range", title_ja: "📅 データの期間を見る", title_en: "📅 See date range", build: () => ({ tool: "describe_data", args: { kind: "date_range" } }) },
  ],
};

export function generateFollowups(
  tool: string,
  args: Record<string, unknown>,
  firstRow: unknown[] | null,
  lang: "ja" | "en",
): FollowupChip[] {
  const templates = TEMPLATES[tool] ?? [];
  const out: FollowupChip[] = [];
  for (const t of templates.slice(0, 5)) {
    let next;
    try { next = t.build(args, firstRow); } catch { continue; }
    if (!next || !next.tool) continue;
    out.push({
      id: t.id,
      title: lang === "ja" ? t.title_ja : t.title_en,
      tool: next.tool,
      args: next.args,
    });
  }
  return out;
}
