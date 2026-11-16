import { explicitScopeQuery } from "../../api/scope";
import type { CardConditions } from "../../api/types";
import { reportHref } from "../../routes/destinations";

/** The screen that answers a card's question in full: the rail screen it
 *  opens on, for the link's label, and the link itself. */
export type CardHome = { screen: "routes" | "time" | "compare"; href: string };

// pipeline/query/tools.py's _TOOL_ALIASES: a card's alias runs the same tool.
const TOOL_ALIASES: Record<string, string> = {
  on_time: "on_time_rate",
  trend: "time_series",
  cmp_service: "compare_segments",
};

// pipeline/query/tools.py's _SERVICE_TYPE_MAP: a ranking card's own service
// type replaces the thread's service filter.
const SERVICE_TYPES: Record<string, string> = {
  weekday: "平日", // i18n-ignore: query contract
  saturday: "土曜", // i18n-ignore: query contract
  sunday_holiday: "日祝", // i18n-ignore: query contract
  weekend: "土日祝", // i18n-ignore: query contract
  all: "all",
};

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** The card's scope as it was answered, or null when the record does not say
 *  which days it covered: a card saved before its conditions kept the
 *  period, which a link to the screen's default days would misstate.
 *
 *  The thread's period and routes come from `conditions`. A card's own dates
 *  win over them, as they do at dispatch; its own route narrows to that one
 *  route, and a ranking card's `service_type` replaces the service filter. */
function answeredScope(
  args: Record<string, unknown>,
  conditions: CardConditions | null | undefined,
  extra: Record<string, string> = {},
): URLSearchParams | null {
  const ownDates = text(args.from_date) != null || text(args.to_date) != null;
  const from = ownDates ? text(args.from_date) : text(conditions?.from_date);
  const to = ownDates ? text(args.to_date) : text(conditions?.to_date);
  if (from == null || to == null) return null;
  const raw = new URLSearchParams({ from, to });
  const set = (key: string, value: string | null) => {
    if (value != null) raw.set(key, value);
  };
  set("dow", text(conditions?.dow));
  set("time_band", text(conditions?.time_band));
  const service = text(args.service_type);
  set("service", service != null && Object.hasOwn(SERVICE_TYPES, service) ? SERVICE_TYPES[service] : text(conditions?.service));
  const route = text(args.route) ?? text(args.route_code);
  set("routes", route ?? (conditions?.routes ?? []).join(","));
  for (const [key, value] of Object.entries(extra)) raw.set(key, value);
  const scope = new URLSearchParams(explicitScopeQuery(raw.toString()));
  return scope.has("from") && scope.has("to") ? scope : null;
}

/** Where a ranking card's routes sit on the Routes table: the list its
 *  metric ranks, in the order the card listed them, over every route
 *  observed often enough for the card to rank it (`sparse=1`, the table's
 *  floor then being the card's). `best_first` defaults as the tools default
 *  it: highest on-time rate first, longest delay first. */
function rankingHome(agencyId: number, metric: unknown, bestFirst: unknown, scope: URLSearchParams | null): CardHome | null {
  if (scope == null) return null;
  scope.set("sparse", "1");
  const m = metric ?? "avg_delay";
  if (m === "worst_5min") return { screen: "routes", href: reportHref(agencyId, "worst_5min", scope.toString()) };
  if (m === "avg_delay") {
    return { screen: "routes", href: reportHref(agencyId, bestFirst === true ? "ranking_best" : "ranking", scope.toString()) };
  }
  if (m === "on_time_rate") {
    if (bestFirst !== false) scope.set("order", "best");
    return { screen: "routes", href: reportHref(agencyId, "on_time", scope.toString()) };
  }
  return null;
}

/** The screen a card's answer is the short form of, over the same scope, or
 *  null when no screen shows the same figures: a card the rail screens have
 *  no question for, a breakdown they draw another way, or a card whose
 *  record does not say which days it covered. */
export function cardHome(
  agencyId: number,
  tool: string | null,
  args: Record<string, unknown> | null,
  conditions: CardConditions | null | undefined,
): CardHome | null {
  if (!tool) return null;
  const a = args ?? {};
  switch (TOOL_ALIASES[tool] ?? tool) {
    case "top_n":
      return rankingHome(agencyId, a.metric, a.best_first, answeredScope(a, conditions));
    case "on_time_rate": {
      // A custom threshold is the scope's late tolerance with every early
      // departure on time, which is how the tool counts it.
      const threshold = a.threshold_min;
      const tolerance =
        typeof threshold === "number" && Number.isInteger(threshold) ? { late: String(threshold * 60), early: "any" } : undefined;
      return rankingHome(agencyId, "on_time_rate", a.best_first ?? true, answeredScope(a, conditions, tolerance));
    }
    case "compare_segments": {
      // Only the weekday-against-weekend split is Compare's; the per-day-type
      // breakdown of one route (Saturday apart from Sunday) is drawn nowhere else.
      if ((a.dimension ?? "dow") !== "dow") return null;
      const scope = answeredScope(a, conditions);
      return scope && { screen: "compare", href: reportHref(agencyId, "compare_ranking", scope.toString()) };
    }
    case "time_series": {
      const scope = answeredScope(a, conditions);
      return scope && { screen: "time", href: reportHref(agencyId, "trend", scope.toString()) };
    }
    default:
      return null;
  }
}
