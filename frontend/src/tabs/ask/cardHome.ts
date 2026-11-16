import { explicitScopeQuery } from "../../api/scope";
import { reportHref } from "../../routes/destinations";

type Conditions = { dow?: string; time_band?: string; service?: string } | null | undefined;

/** The screen that answers a card's question in full: the rail screen it
 *  opens on, for the link's label, and the link itself. */
export type CardHome = { screen: "routes" | "time" | "compare"; href: string };

// pipeline/query/tools.py's _TOOL_ALIASES: a card's alias runs the same tool.
const TOOL_ALIASES: Record<string, string> = {
  on_time: "on_time_rate",
  trend: "time_series",
  cmp_service: "compare_segments",
};

/** The card's own scope, as it was answered: its persisted dates and route,
 *  and the conditions its dispatch ran under, never the conversation's
 *  filters as they stand now. */
function answeredScope(args: Record<string, unknown>, conditions: Conditions, extra: Record<string, string> = {}): URLSearchParams {
  const raw = new URLSearchParams();
  const set = (key: string, value: unknown) => {
    if (typeof value === "string" && value !== "") raw.set(key, value);
  };
  set("from", args.from_date);
  set("to", args.to_date);
  set("dow", conditions?.dow);
  set("time_band", conditions?.time_band);
  set("service", conditions?.service);
  set("routes", args.route ?? args.route_code);
  for (const [key, value] of Object.entries(extra)) raw.set(key, value);
  return new URLSearchParams(explicitScopeQuery(raw.toString()));
}

/** Where a ranking card's routes sit on the Routes table: the list its
 *  metric ranks, in the order the card listed them. `best_first` defaults as
 *  the tools default it: highest on-time rate first, longest delay first. */
function rankingHome(
  agencyId: number,
  metric: unknown,
  bestFirst: unknown,
  scope: URLSearchParams,
): CardHome | null {
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
 *  no question for, or a breakdown they draw another way. */
export function cardHome(
  agencyId: number,
  tool: string | null,
  args: Record<string, unknown> | null,
  conditions: Conditions,
): CardHome | null {
  if (!tool) return null;
  const a = args ?? {};
  switch (TOOL_ALIASES[tool] ?? tool) {
    case "top_n":
      return rankingHome(agencyId, a.metric, a.best_first, answeredScope({ ...a, route: undefined }, conditions));
    case "on_time_rate": {
      // A custom threshold is the scope's late tolerance with every early
      // departure on time, which is how the tool counts it.
      const threshold = a.threshold_min;
      const tolerance =
        typeof threshold === "number" && Number.isInteger(threshold) ? { late: String(threshold * 60), early: "any" } : undefined;
      return rankingHome(agencyId, "on_time_rate", a.best_first ?? true, answeredScope({ ...a, route: undefined }, conditions, tolerance));
    }
    case "compare_segments":
      // Only the weekday-against-weekend split is Compare's; the per-day-type
      // breakdown of one route (Saturday apart from Sunday) is drawn nowhere else.
      if ((a.dimension ?? "dow") !== "dow") return null;
      return { screen: "compare", href: reportHref(agencyId, "compare_ranking", answeredScope(a, conditions).toString()) };
    case "time_series":
      return { screen: "time", href: reportHref(agencyId, "trend", answeredScope(a, conditions).toString()) };
    default:
      return null;
  }
}
