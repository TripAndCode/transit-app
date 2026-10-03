import type { TFunction } from "i18next";
import type { FilterCtx } from "../../api/types";

export type NextStepAction =
  | { kind: "filter"; patch: Partial<FilterCtx> }
  | { kind: "map"; route: string }
  | { kind: "compare_previous"; tool: string; args: Record<string, unknown> }
  | { kind: "export_png" };

type NextStepChip = { id: string; label: string; action: NextStepAction };

/**
 * The "次の一手" (next move) chips for one dispatched Ask answer — each a
 * verb the user can tap immediately, never a question to type. Every chip
 * is optional and independently gated on whether it would actually change
 * anything: no morning-peak chip when already scoped to morning, no map
 * chip without a route in the result's own args, etc.
 *
 * Returns nothing for a dispatch-free message (`tool` is null — the
 * LLM-grounded follow-up path): there is no tool/args to re-run with a
 * narrower filter, no persisted route, and no comparable date window.
 */
export function buildNextStepChips({
  tool,
  args,
  conditions,
  resultKind,
  t,
}: {
  tool: string | null;
  args: Record<string, unknown> | null;
  conditions: { dow?: string; time_band?: string; service?: string } | null;
  resultKind: string | undefined;
  t: TFunction;
}): NextStepChip[] {
  if (!tool) return [];
  const chips: NextStepChip[] = [];

  if (conditions?.time_band !== "morning") {
    chips.push({
      id: "morning_peak",
      label: t("ask.evidence.chip.morning_peak"),
      action: { kind: "filter", patch: { time_band: "morning" } },
    });
  }

  const route = args?.route ?? args?.route_code;
  if (typeof route === "string" && route) {
    chips.push({ id: "view_map", label: t("ask.evidence.chip.view_map"), action: { kind: "map", route } });
  }

  if (typeof args?.from_date === "string" && typeof args?.to_date === "string") {
    chips.push({
      id: "compare_previous",
      label: t("ask.evidence.chip.compare_previous"),
      action: { kind: "compare_previous", tool, args },
    });
  }

  if (resultKind === "series") {
    chips.push({ id: "export_png", label: t("ask.evidence.chip.export_png"), action: { kind: "export_png" } });
  }

  return chips;
}
