import type { TFunction } from "i18next";
import { dowValueLabel, serviceValueLabel, timeBandValueLabel } from "../../utils/filterValueLabels";

type ProvenancePath = "sql" | "llm";

/** A dispatch-free assistant message (no `tool`) only ever comes from the
 *  LLM-grounded follow-up path -- every card/builder dispatch always
 *  carries the tool it ran. See pipeline/query/chat.py's build-sentinel
 *  short-circuit: the guided-form path never calls the LLM. */
export function provenancePath(message: { tool: string | null }): ProvenancePath {
  return message.tool ? "sql" : "llm";
}

// Card-alias tool names (see pipeline/query/tools.py's dispatch()
// `_TOOL_ALIASES`) map to the canonical tool whose label this reuses.
const TOOL_ALIASES: Record<string, string> = {
  trend: "time_series",
  cmp_service: "compare_segments",
  on_time: "on_time_rate",
};

// Tools already labeled for the guided builder UI (ask.routers.ask.py's
// `_BUILD_TOOL_META`) -- reused rather than re-translated.
const BUILD_LABEL_TOOLS = new Set([
  "top_n",
  "time_series",
  "compare_segments",
  "route_stats",
  "route_meta",
  "describe_data",
  "capabilities",
]);

/** Human label for a message's `tool`, for the evidence card's provenance
 *  header/disclosure. `null` when there is no tool to label (the LLM
 *  follow-up path). Falls back to the raw tool name for one this repo adds
 *  later without updating this map. */
export function toolLabel(tool: string | null, t: TFunction): string | null {
  if (!tool) return null;
  const canonical = TOOL_ALIASES[tool] ?? tool;
  if (BUILD_LABEL_TOOLS.has(canonical)) return t(`ask.build_labels.tools.${canonical}`);
  const key = `ask.evidence.tool_label.${canonical}`;
  const label = t(key);
  return label === key ? tool : label;
}

/** The message's dispatch window, from its persisted (canonicalized)
 *  `from_date`/`to_date` args -- `null` when absent (no dispatch, or a tool
 *  whose args carry no date range). */
export function formatWindow(args: Record<string, unknown> | null, t: TFunction): string | null {
  const from = args?.from_date;
  const to = args?.to_date;
  if (typeof from === "string" && typeof to === "string") return t("ask.evidence.window_range", { from, to });
  return null;
}

/** How many observations back the answer, for a table/series result --
 *  `null` for a kv/text/empty result, which carries no row-level count. */
export function sampleCount(result: { rows?: unknown[] | null; series?: unknown[] | null } | null): number | null {
  if (!result) return null;
  if (Array.isArray(result.rows)) return result.rows.length;
  if (Array.isArray(result.series)) return result.series.length;
  return null;
}

/** Renders a message's persisted dispatch conditions (dow/time_band/service)
 *  for the provenance disclosure -- the historical RangeCtx the dispatch
 *  actually ran under, not the conversation's current, editable filter_ctx.
 *  "All conditions" when every dimension was at its default (or the message
 *  carries none, e.g. an LLM follow-up). */
export function conditionsLabel(
  conditions: { dow?: string; time_band?: string; service?: string } | null | undefined,
  t: TFunction,
): string {
  if (!conditions) return t("ask.evidence.conditions_all");
  const parts: string[] = [];
  if (conditions.dow && conditions.dow !== "all") parts.push(dowValueLabel(conditions.dow, t));
  if (conditions.time_band && conditions.time_band !== "all") parts.push(timeBandValueLabel(conditions.time_band, t));
  if (conditions.service && conditions.service !== "all") parts.push(serviceValueLabel(conditions.service, t));
  return parts.length > 0 ? parts.join(" · ") : t("ask.evidence.conditions_all");
}
